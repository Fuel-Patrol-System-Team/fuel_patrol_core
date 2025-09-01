import time
import logging
import requests
from functools import wraps

logger = logging.getLogger(__name__)


# TODO: check
def retry_on_status(max_retries=3, retry_delays=None, status_codes=None):
    if retry_delays is None:
        retry_delays = [5, 10, 20]
    if status_codes is None:
        status_codes = [429]

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while attempt < max_retries:
                try:
                    return func(*args, **kwargs)
                except requests.HTTPError as e:
                    resp = e.response
                    if resp is not None and resp.status_code in status_codes:
                        attempt += 1
                        if attempt < max_retries:
                            delay = retry_delays[attempt - 1]
                            logger.warning(
                                f"Ошибка {resp.status_code}, попытка {attempt}/{max_retries}, жду {delay} сек."
                            )
                            time.sleep(delay)
                            continue
                        else:
                            logger.error("Исчерпаны попытки после ошибки %s", resp.status_code)
                            raise
                    raise
            return None

        return wrapper

    return decorator
