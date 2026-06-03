import polars as pl

from core.models import CarBadData
def maintenance_analysis(df: pl.DataFrame):
    issues = []
    if "ign" in df.columns:
        issues.append(maintenance_sensor_check_ign(df))
    return pl.DataFrame(issues)

def maintenance_fuel_level_check(df: pl.DataFrame, reports = []):
    # spent_fuel
    moving_with_no_loss = df.filter(
        pl.col("pos_s").gt(0) & pl.col("spent_fuel").eq(0)
    )
    for maintenance in moving_with_no_loss.iter_rows(named=True):
        reports.append(
            {
                "event_date": maintenance["timestamp"],
                "message": f"Одинаковый уровень топлива, несмотря на пройденное расстояние {maintenance["name"]}",
                "tags": [CarBadData.Tag.SENSOR,],
                "category": CarBadData.Category.MAINTENANCE,
                "severity": CarBadData.Severity.WARNING,
                
            }
        )
    return reports

def maintenance_critical_raw_fuel_values(df: pl.DataFrame, reports = []):
    # 7000 - crash
    # 65530, 65532, 65535
    critical_values = [65530, 65532, 65535]
    critical_fuel = df.filter(pl.col("calc_sensors_fuel_level").is_in(critical_values))
    critical_fuel = critical_fuel.group_by_dynamic(index_column="timestamp", group_by="auto", every="1d").agg([
        pl.col("calc_sensors_fuel_level").first().alias("critical"),
    ])
    for cricial in critical_fuel.iter_rows(named=True):
        reports.append(
            {
                "event_date": cricial["timestamp"],
                "message": f"Обнаружено критическое значение уровня топлива {cricial["critical"]} для {cricial["auto"]}",
                "tags": [CarBadData.Tag.SENSOR,],
                "category": CarBadData.Category.MAINTENANCE,
                "severity": CarBadData.Severity.ERROR,
                
            }
            
        )
    
    

def maintenance_rpm_slow_change_on_speed(df: pl.DataFrame, reports = []):
    period = pl.col("timestamp").dt.truncate("10m") 
    is_rpm_present = df["rpm"].is_not_null().any()
    is_dtime_present = "dtime" in df.columns
    if is_rpm_present and is_dtime_present:
        check = df.with_columns(
            pl.col("rpm").diff().abs().mean().over(["auto", period ]).alias("rpm_diff")
        )
        check =check.with_columns(
            pl.when(pl.col("pos_s").gt(5) & pl.col("rpm_diff").lt(10) ).then(1).otherwise(0).alias("rpm_value_sus")
        )
        check = check.filter(pl.col("rpm_value_sus").eq(1))
        dtime = check["dtime"].sum()
        if dtime > 0:
            reports.append(
                {
                    "event_date": check["timestamp"].first(),
                    "message": f"Подозрительно стабильный rpm при движении {check["auto"].first()} {check["timestamp"].dt.date().__str__()}",
                    "tags": [CarBadData.Tag.SENSOR],
                    "category": CarBadData.Category.MAINTENANCE,
                    "severity": CarBadData.Severity.WARNING
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
                    "duration": (df["timestamp"].last() - df["timestamp"].first()).total_seconds() / 3600
                }
            elif value == 1 and df["rpm"].is_null().any():
                return {
                    "sensor": "ign",
                    "problem": True,
                    "issue": "Потенциальная проблема с зажиганием",
                    "description": "Датчик зажигания всегда в положении 1, но есть пропуски в данных RPM, что может указывать на проблемы с зажиганием.",
                    "validation_sensor": "rpm",
                    "duration": (df["timestamp"].last() - df["timestamp"].first()).total_seconds() / 3600
                }
    return {
        "sensor": "ign",
        "problem": False,
        "issue": None,
        "description": "Проблем с зажиганием не выявлено.",
        "validation_sensor": None,
        "duration": (df["timestamp"].last() - df["timestamp"].first()).total_seconds() / 3600

        
    }
