import polars as pl

from core.models import CarBadData


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


def maintenance_event_codes(df: pl.DataFrame, reports=[]):
    is_event_codes_column = "event_code" in df.columns
    if is_event_codes_column:
        is_event_codes = df["event_code"].is_not_null().any()
        if is_event_codes:
            important_codes = [
                    (5989, "Зафиксирована остановка объекта.", None),
                    (5899, "Плановый таймер записи (отметка времени в движении или на стоянке).", None),
                    (5900, "Зафиксировано движение с выключенным зажиганием (подозрение на эвакуацию).", CarBadData.Severity.INFO),
                    (5901, "Окончание эвакуации автомобиля.", None),
                    (5906, "Превышение заданного порога скорости.", CarBadData.Severity.WARNING),
                    (5907, "Возврат скорости в нормальный диапазон.", None),
                    (4882, "Напряжение внешнего (бортового) питания упало ниже нормы.", None),
                    (4883, "Бортовое питание восстановилось до рабочего уровня.", 2),
                    (4884, "Разряд встроенной резервной АКБ трекера.", 2),
                    (4885, "Встроенный аккумулятор заряжен.", 2),
                    (5376, "Зажигание включено (триггер по изменению статуса линии зажигания).", None),
                    (5377, "Зажигание выключено.", None),
                    (4113, "Сработал датчик вскрытия корпуса прибора (саботаж).", CarBadData.Severity.WARNING),
                    (4114, "Корпус устройства закрыт, датчик вернулся в норму.", CarBadData.Severity.INFO),
                    (5380, "Общая тревога (нажата физическая SOS-кнопка).", CarBadData.Severity.WARNING),
                    (5379, "Изменение статуса детектора глушения GSM/GNSS-сигналов (Jamming).", None),
                    (5121, "Сработал внутренний акселерометр (детект ДТП или сильного удара).", CarBadData.Severity.WARNING),
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
                df = df.with_columns(pl.col("event_code").is_in([code]).over(period).alias("is_code"))
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
                            "severity": urgency
                        }
                    )
    return reports


def maintenance_critical_raw_fuel_values(df: pl.DataFrame, reports=[]):
    # 7000 - crash
    # 65530, 65532, 65535
    critical_values = [65530, 65532, 65535]
    critical_fuel = df.filter(pl.col("calc_sensors_fuel_level").is_in(critical_values))
    critical_fuel = critical_fuel.group_by_dynamic(
        index_column="timestamp", group_by="auto", every="1d"
    ).agg(
        [
            pl.col("calc_sensors_fuel_level").first().alias("critical"),
        ]
    )
    for cricial in critical_fuel.iter_rows(named=True):
        reports.append(
            {
                "event_date": cricial["timestamp"],
                "message": f"Обнаружено критическое значение уровня топлива {cricial["critical"]} для {cricial["auto"]}",
                "tags": [
                    CarBadData.Tag.SENSOR,
                ],
                "category": CarBadData.Category.MAINTENANCE,
                "severity": CarBadData.Severity.ERROR,
            }
        )


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
