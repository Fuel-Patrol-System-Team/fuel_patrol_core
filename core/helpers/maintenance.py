from ast import alias
from typing import Any, Dict, List

import polars as pl
from polars.exceptions import ColumnNotFoundError

from core.models import CarBadData

DAILY_MESSAGES = 288

def maintenance_analysis(df: pl.DataFrame):
    issues = []
    if "ign" in df.columns:
        issues.append(maintenance_sensor_check_ign(df))
    return pl.DataFrame(issues)


def maintenance_fuel_level_check(df: pl.DataFrame, reports=[]):
    # spent_fuel
    # moving_with_no_loss = df.filter(
    #     pl.col("pos_s").gt(0) & pl.col("spent_fuel").eq(0)
    # )
    # for maintenance in moving_with_no_loss.iter_rows(named=True):
    #     reports.append(
    #         {
    #             "event_date": maintenance["timestamp"],
    #             "message": f"Одинаковый уровень топлива, несмотря на пройденное расстояние {str(maintenance["timestamp"])}",
    #             "tags": [CarBadData.Tag.SENSOR,],
    #             "category": CarBadData.Category.MAINTENANCE,
    #             "severity": CarBadData.Severity.WARNING,

    #         }
    #     )
    return reports

def maintenance_cross_validate_mileage_voltage(df: pl.DataFrame, reports=[]):
    if "mileage" in df.columns and df["mileage"].is_not_null().any():
        dtime_period = pl.col("timestamp").dt.truncate("1h")
        tdf = df.with_columns(
            pl.col("calc_sensors_voltage").diff().abs().mean().over(dtime_period).alias("voltage_diff"),
            pl.col("mileage").diff().abs().sum().over(dtime_period).alias("mileage_diff")
        )
        tdf = tdf.with_columns(
            (pl.col("voltage_diff").lt(15) & pl.col("mileage_diff").gt(10)).cast(pl.Int32).alias("anomaly")
        )
        report_data = (
            tdf.with_columns(pl.col("timestamp").diff().dt.total_hours().alias("mdtime"))
            .group_by_dynamic(index_column="timestamp", every="1d")
            .agg(
                [
                    pl.col("mileage").diff().sum(),
                    pl.col("calc_sensors_voltage").diff().abs().mean(),
                    pl.col("anomaly").sum(),
                    pl.count("anomaly").alias("count")
                ]
            )
        )
        report_data = report_data.filter(pl.col("count").gt(DAILY_MESSAGES) & pl.col("anomaly").truediv(pl.col("count")).gt(0.1))
        for report in report_data.rows(named=True):
            reports.append(
                {
                    "event_date": report["timestamp"],
                    "message": f"Не типично стабильное напряжение бортовой цепи кол-во аномалий {report["anomaly"]}, доля {report["anomaly"] / report["count"]:.2f}",
                    "tags": [CarBadData.Tag.MILEAGE],
                    "category": CarBadData.Category.MAINTENANCE,
                    "severity": CarBadData.Severity.ERROR,
                }
            )
    return reports


def maintenance_fuel_consumpt_check(
    df: pl.DataFrame, sensors: Dict[str, List[Dict[str, Any]]], reports=[]
):
    if df.shape[0] == 0:
        return reports
    if "fuel_consumpt" in sensors and df["fuel_consumpt"].max() > 0:
        report_data = (
            df.with_columns(pl.col("timestamp").diff().dt.total_hours().alias("mdtime"))
            .group_by_dynamic(index_column="timestamp", every="1d")
            .agg(
                [
                    pl.col("mdtime").sum().alias("dtime"),
                    pl.col("fuel_consumpt").diff().sum(),
                    pl.col("pos_s").mean(),
                ]
            )
        )
        pseudo_reports = report_data.filter(
            pl.col("pos_s").gt(2)
            & pl.col("dtime").gt(1)
            & pl.col("fuel_consumpt").lt(1)
        )
        for pseudo_report in pseudo_reports.rows(named=True):
            for sensor in sensors["fuel_consumpt"]:
                reports.append(
                    {
                        "event_date": pseudo_report["timestamp"],
                        "message": f"Потенциальная неисправность датчика расхода топлива {sensor["value"]}",
                        "tags": [CarBadData.Tag.SENSOR],
                        "category": CarBadData.Category.MAINTENANCE,
                        "severity": CarBadData.Severity.ERROR,
                    }
                )
    return reports

def maintenace_check_missing_sensors(df: pl.DataFrame, columns: List[str], sensors: Dict[str, List[Dict[str, Any]]], reports = []):
    for column in columns:
        if column not in sensors:
            continue
        try:
            agg = df.group_by_dynamic(
                index_column="timestamp", every="1d"
            ).agg(
                [
                    pl.count("timestamp").alias("count"),
                    pl.count(column).alias("working_count")
                ]
            )
            agg = agg.filter(pl.col("count").gt(100) & pl.col("working_count").truediv(pl.col("count")).lt(0.05))
            for ag in agg.rows(named=True):
                reports.append(
                    {
                        "event_date": ag["timestamp"],
                        "message": f"Потенциальная неисправность датчика {column} (параметр {sensors[column][-1]})",
                        "tags": [CarBadData.Tag.SENSOR],
                        "category": CarBadData.Category.MAINTENANCE,
                        "severity": CarBadData.Severity.ERROR,
                    }
                )
        except ColumnNotFoundError:
            return reports
    return reports

def maintenance_mileage_sensor_check(
    df: pl.DataFrame, sensors: Dict[str, List[Dict[str, Any]]], reports=[]
):
    if "mileage" in df.columns:
        report_data = (
            df.filter(pl.col("speed_gps").gt(0))
            .with_columns(pl.col("timestamp").diff().dt.total_hours().alias("mdtime"))
            .group_by_dynamic(index_column="timestamp", every="1d")
            .agg(
                [
                    pl.col("mdtime").sum(),
                    pl.col("mileage").diff().sum().abs(),
                    pl.col("speed_gps").mean(),
                ]
            )
        )
        pseudo_reports = report_data.filter(
            pl.col("mileage").lt(10)
            & pl.col("mileage").lt(pl.col("speed_gps").mul(pl.col("mdtime")))
        )
        for pseudo_report in pseudo_reports.rows(named=True):
            for sensor in sensors["mileage"]:
                reports.append(
                    {
                        "event_date": pseudo_report["timestamp"],
                        "message": f"Потенциальная неисправность датчика пробега (параметр {sensor["value"]}) (слишком малый пробег)",
                        "tags": [CarBadData.Tag.SENSOR],
                        "category": CarBadData.Category.MAINTENANCE,
                        "severity": CarBadData.Severity.ERROR,
                    }
                )
    return reports


def maintenance_event_codes(df: pl.DataFrame, reports=[]):
    is_event_codes_column = "event_code" in df.columns
    if is_event_codes_column:
        is_event_codes = df["event_code"].is_not_null().any()
        if is_event_codes:
            important_codes = [
                (5989, "Зафиксирована остановка объекта.", None),
                (
                    5899,
                    "Плановый таймер записи (отметка времени в движении или на стоянке).",
                    None,
                ),
                (
                    5900,
                    "Зафиксировано движение с выключенным зажиганием (подозрение на эвакуацию).",
                    CarBadData.Severity.INFO,
                ),
                (5901, "Окончание эвакуации автомобиля.", None),
                (
                    5906,
                    "Превышение заданного порога скорости.",
                    CarBadData.Severity.WARNING,
                ),
                (5907, "Возврат скорости в нормальный диапазон.", None),
                (
                    4882,
                    "Напряжение внешнего (бортового) питания упало ниже нормы.",
                    None,
                ),
                (4883, "Бортовое питание восстановилось до рабочего уровня.", 2),
                (4884, "Разряд встроенной резервной АКБ трекера.", 2),
                (4885, "Встроенный аккумулятор заряжен.", 2),
                (
                    5376,
                    "Зажигание включено (триггер по изменению статуса линии зажигания).",
                    None,
                ),
                (5377, "Зажигание выключено.", None),
                (
                    4113,
                    "Сработал датчик вскрытия корпуса прибора (саботаж).",
                    CarBadData.Severity.WARNING,
                ),
                (
                    4114,
                    "Корпус устройства закрыт, датчик вернулся в норму.",
                    CarBadData.Severity.INFO,
                ),
                (
                    5380,
                    "Общая тревога (нажата физическая SOS-кнопка).",
                    CarBadData.Severity.WARNING,
                ),
                (
                    5379,
                    "Изменение статуса детектора глушения GSM/GNSS-сигналов (Jamming).",
                    None,
                ),
                (
                    5121,
                    "Сработал внутренний акселерометр (детект ДТП или сильного удара).",
                    CarBadData.Severity.WARNING,
                ),
                (4353, "Вход №1 активирован (замкнулся / пошел сигнал).", None),
                (4356, "Вход №1 вернулся в исходное состояние (разомкнулся).", None),
                (4369, "Вход №2 активирован.", None),
                (4372, "Вход №2 вернулся в норму.", None),
                (4385, "Вход №3 активирован.", None),
                (4388, "Вход №3 вернулся в норму.", None),
                (4401, "Вход №4 активирован.", None),
                (5936, "Превышение скорости. Порог 1.", None),
                (5937, "Скорость пришла в норму после Порога 1.", None),
                (5942, "Зафиксировано опасное (резкое) ускорение.", None),
                (5945, "Зафиксировано опасное (резкое) торможение.", None),
                (5948, "Зафиксирован резкий и опасный поворот (боковой занос).", None),
            ]
            period = pl.col("timestamp").dt.truncate("1h")
            for code, description, urgency in important_codes:
                if urgency is None:
                    continue
                df = df.with_columns(
                    pl.col("event_code").is_in([code]).over(period).alias("is_code")
                )
                code_df = df.filter(pl.col("is_code"))
                for row in code_df.iter_rows(named=True):
                    timestamp = row["timestamp"]
                    reports.append(
                        {
                            "event_date": timestamp,
                            "message": f"{description}",
                            "tags": [
                                CarBadData.Tag.MAINTENANCE,
                            ],
                            "category": CarBadData.Category.DATA_QUALITY,
                            "severity": urgency,
                        }
                    )
    return reports


def maintenance_board_voltage_notify(df: pl.DataFrame, sensors, reports=[]):
    if "calc_sensors_voltage" in df.columns:
        if df["calc_sensors_voltage"].is_not_null().any():
            pvf = df.with_columns(
                pl.col("calc_sensors_voltage")
                .mean()
                .over([pl.col("timestamp").dt.truncate("1d")])
                .alias("voltage_mean")
            ).with_columns(
                pl.col("calc_sensors_voltage")
                .sub(pl.col("voltage_mean"))
                .abs()
                .alias("voltage_diff")
            ).with_columns(
                pl.col("voltage_diff").gt(9000).cast(pl.Int32).alias("voltage_jumps")
            )

            voltage_reports = pvf.group_by_dynamic(
                index_column="timestamp", every="1d"
            ).agg(
                [
                    pl.col("voltage_diff").max(),
                    pl.col("voltage_jumps").sum(),
                    pl.col("calc_sensors_voltage").count().alias("count"),
                ]
            )
            voltage_reports = voltage_reports.filter(
                pl.col("voltage_jumps").ge(1) & pl.col("voltage_diff").gt(9000)
            )
            for voltage_report in voltage_reports.iter_rows(named=True):
                reports.append(
                    {
                        "event_date": voltage_report["timestamp"],
                        "message": f"Потенциальная неполадка датчика напряжения. Обнаружены скачки напряжения выше 9 вольт {voltage_report["voltage_diff"]:.1f}, кол-во {voltage_report["voltage_jumps"]:.1f}, {(voltage_report["voltage_jumps"] / voltage_report["count"]) * 100:.2f}% ",
                        "tags": [
                            CarBadData.Tag.SENSOR,
                        ],
                        "category": CarBadData.Category.MAINTENANCE,
                        "severity": CarBadData.Severity.ERROR,
                    }
                )

    return reports


def maintenance_critical_raw_fuel_values(df: pl.DataFrame, sensors: Dict[str, List[Dict[str, Any]]],  reports=[]):
    # 7000 - crash
    # 65530, 65532, 65535
    if "calc_sensors_fuel_level" not in sensors:
        return reports
    fuel_sensors = list(filter(lambda x: "calc_sensors_fuel_level" in x, df.columns))
    if len(fuel_sensors) == 0:
        return reports
    fuel_sensors_names = sensors["calc_sensors_fuel_level"]
    for i, sensor in  enumerate(fuel_sensors):
        critical_values = [65530, 65531, 65532, 65533, 65535]
        critical_fuel = df.with_columns(
            [
                pl.col(sensor)
                .is_in(critical_values)
                .cast(pl.Int32)
                .alias("critical")
            ]
        )
        critical_fuel = critical_fuel.with_columns(
            pl.when(pl.col("critical").eq(1))
            .then(pl.col(sensor))
            .otherwise(None)
            .alias("critical_value")
        )
        critical_fuel = critical_fuel.group_by_dynamic(
            index_column="timestamp", group_by="auto", every="1d"
        ).agg(
            [
                pl.col("critical_value").min().alias("critical_value"),
                pl.col("critical").sum().alias("critical_amount"),
                pl.col(sensor).count().alias("total_amount"),
            ]
        )

        for critical in critical_fuel.iter_rows(named=True):
            if critical["total_amount"] == 0:
                continue
            if critical["critical_amount"] / critical["total_amount"] > 0.1:
                reports.append(
                    {
                        "event_date": critical["timestamp"],
                        "message": f"Потенциальная неполадка датчика уровня топлива. Обнаружены критическое значения {fuel_sensors_names[i]["value"]} {critical["critical_value"]}, кол-во {critical["critical_amount"]}, {(critical["critical_amount"] / critical["total_amount"]) * 100}% ",
                        "tags": [
                            CarBadData.Tag.SENSOR,
                        ],
                        "category": CarBadData.Category.MAINTENANCE,
                        "severity": CarBadData.Severity.ERROR,
                    }
                )
    return reports


def maintenance_rpm_slow_change_on_speed(df: pl.DataFrame, reports=[]):
    period = pl.col("timestamp").dt.truncate("10m")
    is_rpm_present = df["rpm"].is_not_null().any()
    is_dtime_present = "dtime" in df.columns
    if is_rpm_present and is_dtime_present:
        check = df.with_columns(
            pl.col("rpm").diff().abs().mean().over(["auto", period]).alias("rpm_diff")
        )
        check = check.with_columns(
            pl.when(pl.col("pos_s").gt(5) & pl.col("rpm_diff").lt(10))
            .then(1)
            .otherwise(0)
            .alias("rpm_value_sus")
        )
        check = check.filter(pl.col("rpm_value_sus").eq(1))
        dtime = check["dtime"].sum()
        if dtime > 0:
            reports.append(
                {
                    "event_date": check["timestamp"].first(),
                    "message": f"Подозрительно стабильный rpm при движении {check["auto"].first()} {check["timestamp"].dt.date().first().__str__()}",
                    "tags": [CarBadData.Tag.SENSOR],
                    "category": CarBadData.Category.MAINTENANCE,
                    "severity": CarBadData.Severity.WARNING,
                }
            )
    return reports


def maintenance_sensor_check_ign(df: pl.DataFrame):
    """
    Анализ данных для выявления возможных проблем с зажиганием.
    Например, если датчик в положении 0/1 несмотря на то, что автомобиль движется, это может указывать на проблему с зажиганием.
    """
    # Пример: выявление резких изменений в данных
    value = df["ign"].max()
    if df["ign"].eq(value).all():
        if "rpm" in df.columns:
            if value == 0 and df["rpm"].mean() > 0:
                return {
                    "sensor": "ign",
                    "problem": True,
                    "issue": "Потенциальная проблема с зажиганием",
                    "description": "Датчик зажигания всегда в положении 0, несмотря на движение автомобиля (RPM > 0).",
                    "validation_sensor": "rpm",
                    "duration": (
                        df["timestamp"].last() - df["timestamp"].first()
                    ).total_seconds()
                    / 3600,
                }
            elif value == 1 and df["rpm"].is_null().any():
                return {
                    "sensor": "ign",
                    "problem": True,
                    "issue": "Потенциальная проблема с зажиганием",
                    "description": "Датчик зажигания всегда в положении 1, но есть пропуски в данных RPM, что может указывать на проблемы с зажиганием.",
                    "validation_sensor": "rpm",
                    "duration": (
                        df["timestamp"].last() - df["timestamp"].first()
                    ).total_seconds()
                    / 3600,
                }
    return {
        "sensor": "ign",
        "problem": False,
        "issue": None,
        "description": "Проблем с зажиганием не выявлено.",
        "validation_sensor": None,
        "duration": (df["timestamp"].last() - df["timestamp"].first()).total_seconds()
        / 3600,
    }
