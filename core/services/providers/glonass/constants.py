from enum import Enum
from typing import Any, Dict, Literal, TypedDict
from attr import dataclass
import polars as pl
from typing import Protocol

from core.helpers.fuel import reconcile_multisensor, tarify_car_by_sensor
from core.models import Car 

class SensorMappingParserType(TypedDict):
    value: str
    multi: bool
    multi_type: Literal["can"] | Literal["tank"] | Literal["none"]
    metadata: Dict[str, Any]
class GlonassCastProtocol(Protocol): 
    def __call__(self, df: pl.DataFrame, sensor_mapping: dict[str, list[SensorMappingParserType]]) -> pl.DataFrame:
        ...
class GlonassAfterParsingProtocol(Protocol):
    def __call__(self, df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, list[SensorMappingParserType]]) ->pl.DataFrame:
        ...
@dataclass
class GlonassParameter:
    is_dynamic: bool
    default_key: str
    label: str
    cast: GlonassCastProtocol | None
    filter_on_absence: bool
    default_value: Any
    default_on_absence: Any

class GL_PARAM_KEYS(Enum):
    timestamp = "timestamp"
    timestamp_server = "timestamp_server"
    speed = "speed"
    speed_gps = "speed_gps"
    fuel_level = "calc_sensors_fuel_level"
    mileage = "mileage"
    motohours = "motohours"
    ignition = "ign"
    rpm = "rpm"
    engine_temp = "engine_temp"
    voltage = "voltage"
    latitude = "latitude"
    longitude = "longitude"
    amtr_x= "amtr_x"
    amtr_y= "amtr_y"
    amtr_z= "amtr_z"
    satellites = "satellites"
    msg_number = "msg_number"
    fuel_consumpt = "fuel_consumpt"
    rpm_idle = "rpm_idle"
    rpm_active = "rpm_active"
    event_code = "event_code"
    
class GL_ACTION_KEYS(Enum):
    tarify_car = "tarify"
    auto_column = "auto"
    amtr_merge = "amtr_merge"
    chart_preprocess = "chart_preprocess"


def _cast_ign(df: pl.DataFrame, sensor_mapping: dict[str, list[SensorMappingParserType]]):
    if "ign" in df.columns:
        if df["ign"].dtype == pl.Boolean:
            df = df.with_columns(pl.col("ign").cast(pl.Int32))
            return df

    sensor = sensor_mapping[GL_PARAM_KEYS.ignition.value][0]
    ign_bit = 0
    if sensor['metadata'] is not None:
        ign_bit = sensor["metadata"].get("iobit", 0)
    ign_bit = (-1 * ign_bit) -1
    df = df.with_columns(pl.col("ign").cast(pl.String).str.slice(ign_bit, 1).cast(pl.Int32).clip(upper_bound=1))
    return df

GLOBAL_GLONASS_PARAMS: dict[GL_PARAM_KEYS, GlonassParameter] = {
    GL_PARAM_KEYS.timestamp : GlonassParameter(False, "deviceTime", "timestamp",lambda df, sensor_mapping: df.with_columns(pl.col("timestamp").cast(pl.Datetime)),True, None, None),
    GL_PARAM_KEYS.timestamp_server : GlonassParameter(False, "serverTime", "timestamp_server",lambda df, sensor_mapping: df.with_columns(pl.col("timestamp_server").cast(pl.Datetime)),True, None, None),
    GL_PARAM_KEYS.speed: GlonassParameter(True, "speed", "pos_s", None, False, 0, None ),
    GL_PARAM_KEYS.speed_gps: GlonassParameter(False, "speed", "speed_gps", None, False, 0, None ),
    GL_PARAM_KEYS.fuel_level: GlonassParameter(True, "", "calc_sensors_fuel_level", None, False, None, None ),
    GL_PARAM_KEYS.mileage: GlonassParameter(True, "", "mileage", None, False, 0, None),
    GL_PARAM_KEYS.motohours: GlonassParameter(True, "", "motohours", None, False, None, None),
    GL_PARAM_KEYS.ignition: GlonassParameter(True, "", "ign",_cast_ign, False, 0, None),
    GL_PARAM_KEYS.rpm: GlonassParameter(True, "", "rpm", None, False, None, None),
    GL_PARAM_KEYS.engine_temp: GlonassParameter(True, "", "engine_temp", None, False, 0, None),
    GL_PARAM_KEYS.voltage: GlonassParameter(False, "voltage", "calc_sensors_voltage", None, False, None, None),
    GL_PARAM_KEYS.latitude: GlonassParameter(False, "latitude", "latitude", None, False, None, None),
    GL_PARAM_KEYS.longitude: GlonassParameter(False, "longitude", "longitude", None, False, None, None),
    GL_PARAM_KEYS.amtr_x: GlonassParameter(False, "amtr_x", "amtr_x", None, False, 0, 0),
    GL_PARAM_KEYS.amtr_y: GlonassParameter(False, "amtr_y", "amtr_y", None, False, 0, 0),
    GL_PARAM_KEYS.amtr_z: GlonassParameter(False, "amtr_z", "amtr_z", None, False, 0,0),
    GL_PARAM_KEYS.satellites: GlonassParameter(False, "satellites", "satellites",lambda df, sensor_mapping: df.with_columns(pl.col("satellites").cast(pl.Int8)),False, None, None),
    GL_PARAM_KEYS.msg_number: GlonassParameter(True, "parameters.msg_number", "msg_number", None, False, 999, 999),
    GL_PARAM_KEYS.event_code: GlonassParameter(True, "parameters.event_code", "event_code", None, False, None, None),
    GL_PARAM_KEYS.fuel_consumpt: GlonassParameter(True, "", "fuel_consumpt", None,  False, 0, 0),
    GL_PARAM_KEYS.rpm_idle: GlonassParameter(True, "", "rpm_idle", None, False, 0, 0),
    GL_PARAM_KEYS.rpm_active: GlonassParameter(True, "", "rpm_active", None, False, 0, 0),
    }
    

    

def _modify_auto(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, list[SensorMappingParserType]]):
    df = df.with_columns(pl.lit(str(car.id)).alias("auto").cast(pl.Categorical)) 
    mapping.append("auto")
    return df


def _merge_amtr(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, list[SensorMappingParserType]]):
    df = df.with_columns(pl.col("amtr_x").add(pl.col("amtr_y")).add(pl.col("amtr_z")).alias("amtr")) 
    mapping.remove("amtr_x")
    mapping.remove("amtr_y")
    mapping.remove("amtr_z")
    mapping.append("amtr")
    return df

def _tarify_car(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, list[SensorMappingParserType]]):

    sensors = filter(lambda c: c.startswith("calc_sensors_fuel_level"), df.columns)



    for i, sensor in enumerate(sensors):
        grades = sensor_mapping["calc_sensors_fuel_level"][i].get("metadata", {}).get("grades", None)
        unique = list({tuple(sorted(d.items())): d for d in grades}.values())
        pairs = list(zip(unique, unique[1:]))
        mp = unique[0]
        lp = unique[-1]
        for fp, sp in pairs:
            slope = (sp["output"] - fp["output"]) / (sp["input"] - fp["input"])
            b = fp["output"] - slope * fp["input"]
            df = df.with_columns(
                pl.when(
                    pl.col(sensor).is_between(fp["input"], sp["input"])
                )
                .then(pl.col(sensor).mul(slope).add(b))
                .otherwise(pl.col(sensor))
            )
        df = df.filter(pl.col(sensor).ge(mp["input"]))
        df = df.filter(pl.col(sensor).le(lp["input"]))
    return df

def _default(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, list[SensorMappingParserType]]):
    if "flex_adc" in sensor_mapping["calc_sensors_fuel_level"]:
        df = df.filter(~pl.col("calc_sensors_fuel_level").is_in([9, 4]))
    print(sensor_mapping)
    return df

    

def _chart_preprocess(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, list[SensorMappingParserType]]):
    sensors = list(filter(lambda c: c.startswith("calc_sensors_fuel_level"), df.columns))

    if "msg_number" in df.columns:
        df = df.filter(pl.col("msg_number").diff().abs().fill_null(0).fill_nan(0).lt(5))

    for i, sensor in enumerate(sensors):
        df = df.filter(pl.col(sensor).gt(0))
        # if "flex_adc" in sensor_mapping["calc_sensors_fuel_level"]:
        #     df = df.filter(~pl.col(sensor).is_in([9, 4]))
        df, lp, b, slop  = tarify_car_by_sensor(df, {"grades": sensor_mapping["calc_sensors_fuel_level"][i]["metadata"]["grades"] }, sensor)
    df = reconcile_multisensor(df, sensor_mapping["calc_sensors_fuel_level"][-1]["multi_type"])
    if "calc_sensors_fuel_level" not in mapping:
        mapping.append("calc_sensors_fuel_level")
    return df
    
GLOBAL_GLONASS_ACTIONS: dict[GL_ACTION_KEYS, GlonassAfterParsingProtocol] = {
    GL_ACTION_KEYS.tarify_car: _tarify_car,
    GL_ACTION_KEYS.auto_column: _modify_auto,
    GL_ACTION_KEYS.amtr_merge: _merge_amtr,
    GL_ACTION_KEYS.chart_preprocess: _chart_preprocess,
}