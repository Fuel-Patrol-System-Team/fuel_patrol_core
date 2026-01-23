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
    def __call__(self, df: pl.DataFrame, car: Car) -> pl.DataFrame:
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
    fuel_level = "fuel"
    mileage = "mileage"
    motohours = "motohours"
    ignition = "ign"
    rpm = "rpm"
    engine_temp = "engine_temp"
    voltage = "voltage"
    latitude = "latitude"
    longitude = "longitude"
    satellites = "satellites"
    
class GL_ACTION_KEYS(Enum):
    tarify_car = "tarify"

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
    GL_PARAM_KEYS.satellites: GlonassParameter(False, "satellites", "satellites",lambda df: df.with_columns(pl.col("satellites").cast(pl.Int8)),False, None, None), }


GLOBAL_GLONASS_ACTIONS: dict[GL_ACTION_KEYS, GlonassAfterParsingProtocol] = {
    GL_ACTION_KEYS.tarify_car: lambda df, car: df.with_columns(pl.col("calc_sensors_fuel_level").mul(car.output).truediv(car.input)),
}