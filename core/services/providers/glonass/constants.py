from enum import Enum
from typing import Any
from attr import dataclass
import polars as pl
from typing import Protocol

from core.models import Car 

class GlonassCastProtocol(Protocol): 
    def __call__(self, df: pl.DataFrame) -> pl.DataFrame:
        ...
class GlonassAfterParsingProtocol(Protocol):
    def __call__(self, df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, str]) ->pl.DataFrame:
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
    speed = "speed"
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
    
class GL_ACTION_KEYS(Enum):
    tarify_car = "tarify"
    auto_column = "auto"
    amtr_merge = "amtr_merge"
    chart_preprocess = "chart_preprocess"

GLOBAL_GLONASS_PARAMS: dict[GL_PARAM_KEYS, GlonassParameter] = {
    GL_PARAM_KEYS.timestamp : GlonassParameter(False, "deviceTime", "timestamp",lambda df: df.with_columns(pl.col("timestamp").cast(pl.Datetime)),True, None, None),
    GL_PARAM_KEYS.speed: GlonassParameter(True, "speed", "pos_s", None, False, 0, None ),
    GL_PARAM_KEYS.fuel_level: GlonassParameter(True, "", "calc_sensors_fuel_level", None, False, None, None ),
    GL_PARAM_KEYS.mileage: GlonassParameter(True, "", "mileage", None, False, 0, None),
    GL_PARAM_KEYS.motohours: GlonassParameter(True, "", "motohours", None, False, None, None),
    GL_PARAM_KEYS.ignition: GlonassParameter(True, "", "ign", lambda df: df.with_columns(pl.col("ign").cast(pl.Int32).clip(upper_bound=1)), False, 0, None),
    GL_PARAM_KEYS.rpm: GlonassParameter(True, "", "rpm", None, False, None, None),
    GL_PARAM_KEYS.engine_temp: GlonassParameter(True, "", "engine_temp", None, False, 0, None),
    GL_PARAM_KEYS.voltage: GlonassParameter(False, "voltage", "calc_sensors_voltage", None, False, None, None),
    GL_PARAM_KEYS.latitude: GlonassParameter(False, "latitude", "latitude", None, False, None, None),
    GL_PARAM_KEYS.longitude: GlonassParameter(False, "longitude", "longitude", None, False, None, None),
    GL_PARAM_KEYS.amtr_x: GlonassParameter(False, "amtr_x", "amtr_x", None, False, 0, 0),
    GL_PARAM_KEYS.amtr_y: GlonassParameter(False, "amtr_y", "amtr_y", None, False, 0, 0),
    GL_PARAM_KEYS.amtr_z: GlonassParameter(False, "amtr_z", "amtr_z", None, False, 0,0),
    GL_PARAM_KEYS.satellites: GlonassParameter(False, "satellites", "satellites",lambda df: df.with_columns(pl.col("satellites").cast(pl.Int8)),False, None, None), }


def _modify_auto(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, str]):
    df = df.with_columns(pl.lit(str(car.id)).alias("auto").cast(pl.Categorical)) 
    mapping.append("auto")
    return df

def _merge_amtr(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, str]):
    df = df.with_columns(pl.col("amtr_x").add(pl.col("amtr_y")).add(pl.col("amtr_z")).alias("amtr")) 
    mapping.remove("amtr_x")
    mapping.remove("amtr_y")
    mapping.remove("amtr_z")
    mapping.append("amtr")
    return df

def _tarify_car(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, str]):
    grades = car.grades
    unique = list({tuple(sorted(d.items())): d for d in grades["grades"]}.values())
    pairs = list(zip(unique, unique[1:]))
    mp = unique[0]
    lp = unique[-1]
    for fp, sp in pairs:
        slope = (sp["output"] - fp["output"]) / (sp["input"] - fp["input"])
        b = fp["output"] - slope * fp["input"]
        df = df.with_columns(
            pl.when(
                pl.col("calc_sensors_fuel_level").is_between(fp["input"], sp["input"])
            )
            .then(pl.col("calc_sensors_fuel_level").mul(slope).add(b))
            .otherwise(pl.col("calc_sensors_fuel_level"))
        )
    df = df.filter(pl.col("calc_sensors_fuel_level").ge(mp["input"]))
    df = df.filter(pl.col("calc_sensors_fuel_level").le(lp["input"]))
    return df

def _default(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, str]):
    if "flex_adc" in sensor_mapping["calc_sensors_fuel_level"]:
        df = df.filter(~pl.col("calc_sensors_fuel_level").is_in([9, 4]))
    print(sensor_mapping)
    return df

def _chart_preprocess(df: pl.DataFrame, car: Car, mapping: list[str], sensor_mapping: dict[str, str]):
    df = df.filter(pl.col("calc_sensors_fuel_level").gt(0))
    if "flex_adc" in sensor_mapping["calc_sensors_fuel_level"]:
        df = df.filter(~pl.col("calc_sensors_fuel_level").is_in([9, 4]))
    df = _tarify_car(df, car, mapping, sensor_mapping)
    return df
    
GLOBAL_GLONASS_ACTIONS: dict[GL_ACTION_KEYS, GlonassAfterParsingProtocol] = {
    GL_ACTION_KEYS.tarify_car: _tarify_car,
    GL_ACTION_KEYS.auto_column: _modify_auto,
    GL_ACTION_KEYS.amtr_merge: _merge_amtr,
    GL_ACTION_KEYS.chart_preprocess: _chart_preprocess,
}