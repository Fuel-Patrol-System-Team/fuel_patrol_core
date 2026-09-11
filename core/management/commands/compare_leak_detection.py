"""
Сверка методов детекции утечек (текущая rule-based система + 10 Lasso-сценариев)
против РЕАЛЬНЫХ CarReport из БД для организации.

Использование:
    python manage.py compare_leak_detection --org-id <uuid> --date-start 2026-01-01 \\
        --detail-csv data/ml_training/lasso_scenarios_detail.csv
"""
from datetime import datetime, timezone as py_timezone
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from core.models import CarReport


class Command(BaseCommand):
    help = "Сверяет методы детекции утечек против реальных CarReport из БД"

    def add_arguments(self, parser):
        parser.add_argument("--org-id", type=str, required=True)
        parser.add_argument("--date-start", type=str, required=True)
        parser.add_argument("--date-end", type=str, default=None)
        parser.add_argument("--detail-csv", type=str, default="data/ml_training/lasso_scenarios_detail.csv")
        parser.add_argument("--tolerance-hours", type=int, default=2, help="Допуск сопоставления по времени (часы)")
        parser.add_argument("--out-dir", type=str, default="data/ml_training")

    def handle(self, *args, **options):
        org_id = options["org_id"]
        start = datetime.fromisoformat(options["date_start"]).astimezone(py_timezone.utc)
        end = (
            datetime.fromisoformat(options["date_end"]).astimezone(py_timezone.utc)
            if options["date_end"] else None
        )

        qs = CarReport.objects.filter(car_id__data_providers__org_id=org_id, datetime__gte=start)
        if end:
            qs = qs.filter(datetime__lt=end)
        qs = qs.select_related("car_id")

        real = pd.DataFrame.from_records(
            qs.values("car_id", "datetime", "volume", "picked_by", "status")
        )
        if real.empty:
            raise CommandError("В БД нет CarReport для этих org-id/периода")
        real["car_id"] = real["car_id"].astype(str)
        real["datetime"] = pd.to_datetime(real["datetime"], utc=True)
        self.stdout.write(self.style.NOTICE(f"Реальных CarReport в БД: {len(real)}"))

        detail_path = Path(options["detail_csv"])
        if not detail_path.exists():
            raise CommandError(f"Не найден {detail_path}")
        detail = pd.read_csv(detail_path, low_memory=False)
        detail["car_id"] = detail["car_id"].astype(str)
        detail["timestamp"] = pd.to_datetime(detail["timestamp"], utc=True)
        detail["is_picked_leak_current"] = detail["is_picked_leak_current"].astype(str) == "True"

        flag_cols = [c for c in detail.columns if c.endswith("_flag")]
        method_cols = ["is_picked_leak_current"] + flag_cols

        tolerance = pd.Timedelta(hours=options["tolerance_hours"])

        # Сопоставляем каждый реальный CarReport с ближайшей по времени строкой
        # в собранных данных ЭТОЙ ЖЕ машины, в пределах допуска.
        matched_rows = []
        unmatched = []
        for car_id, real_group in real.groupby("car_id"):
            cand = detail[detail["car_id"] == car_id].sort_values("timestamp")
            if cand.empty:
                unmatched.extend(real_group.to_dict("records"))
                continue
            for _, rep in real_group.iterrows():
                diffs = (cand["timestamp"] - rep["datetime"]).abs()
                idx = diffs.idxmin()
                if diffs.loc[idx] <= tolerance:
                    row = cand.loc[idx].to_dict()
                    row["real_datetime"] = rep["datetime"]
                    row["real_volume"] = rep["volume"]
                    row["real_picked_by"] = rep["picked_by"]
                    row["time_diff_minutes"] = diffs.loc[idx].total_seconds() / 60
                    matched_rows.append(row)
                else:
                    unmatched.append(rep.to_dict())

        matched = pd.DataFrame(matched_rows)
        self.stdout.write(
            f"Найдено соответствие в собранных данных: {len(matched)}/{len(real)} "
            f"(допуск {options['tolerance_hours']}ч). Без соответствия: {len(unmatched)}"
        )

        # Метрики recall по каждому методу относительно реальных CarReport
        summary = []
        for col in method_cols:
            if col not in matched.columns:
                continue
            recall_hits = int(matched[col].astype(bool).sum()) if not matched.empty else 0
            total_flagged = int(detail[col].astype(bool).sum())
            precision_hits = recall_hits  # из отфлагованных, сколько реально подтверждено CarReport
            summary.append({
                "method": col,
                "recall_hits_of_114": recall_hits,
                "recall_pct": round(100 * recall_hits / len(real), 1),
                "total_flagged_in_dataset": total_flagged,
                "precision_pct_of_flagged": round(100 * precision_hits / total_flagged, 1) if total_flagged else None,
            })

        summary_df = pd.DataFrame(summary).sort_values("recall_hits_of_114", ascending=False)

        out_dir = Path(options["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)

        summary_path = out_dir / "production_comparison_summary.csv"
        summary_df.to_csv(summary_path, index=False)

        matched_path = out_dir / "production_matched_detail.csv"
        matched.to_csv(matched_path, index=False)

        unmatched_path = out_dir / "production_unmatched.csv"
        pd.DataFrame(unmatched).to_csv(unmatched_path, index=False)

        self.stdout.write(self.style.SUCCESS("\n" + summary_df.to_string(index=False)))
        self.stdout.write(self.style.SUCCESS(
            f"\nФайлы: {summary_path}, {matched_path}, {unmatched_path}"
        ))
