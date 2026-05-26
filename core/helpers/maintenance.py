import polars as pl
def maintenance_analysis(df: pl.DataFrame):
    issues = []
    if "ign" in df.columns:
        issues.append(maintenance_sensor_check_ign(df))
    return pl.DataFrame(issues)

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
