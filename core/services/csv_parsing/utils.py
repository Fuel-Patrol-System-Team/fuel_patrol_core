import pandas as pd
import numpy as np
from pathlib import Path


def parse_norma(path: Path) -> pd.DataFrame:
    """
        Возвращает датафрейм с колонками auto, winter_norm, summer_norm, due
        due - дата до которой работает норма

        Args:
            path (Path): путь до файла
        Returns:
            Dataframe: датафрейм

    """
    norms = pd.read_csv(path)
    norms = norms.groupby(['sl_avto', 'vid_norm_rasx']).last().reset_index()
    norms['vid_norm_rasx'].unique()
    NORM_SUMMER = 'Норма на 100 км (Норма за час для ТС по моточасам) Летняя'
    NORM_WINTER = 'Норма на 100 км (Норма за час для ТС по моточасам) Зимняя'
    norms = norms[norms['vid_norm_rasx'].isin([NORM_SUMMER, NORM_WINTER])]
    t = {}
    for _, row in norms.iterrows():
        if row[0] not in t:
            t[row[0]] = [-1, -1, "2000-12-31 21:00:00"]

        if row[1] == NORM_SUMMER:
            t[row[0]][0] = row[3]
            t[row[0]][2] = row[4]
        if row[1] == NORM_WINTER:
            t[row[0]][1] = row[3]
            t[row[0]][2] = row[4]
    result_list = []
    for m in t.items():
        result_list.append((m[0], *m[1]))

    # columns выходные колонки
    result = pd.DataFrame(result_list, columns=['auto', 'winter_norm', 'summer_norm', 'due'])


def parse_cars(path: Path):
    df = pd.read_csv(path, usecols=['guid', 'gos_nomer', 'vin_nomer', 'marka', 'model', 'description'])
    df['marka'].replace(np.nan, "", inplace=True)
    df['model'].replace(np.nan, "", inplace=True)
    df['vin_nomer'].replace(np.nan, "", inplace=True)
    df['gos_nomer'].replace(np.nan, "", inplace=True)
    df['name'] = df['marka'] + " " + df['model'] + " " + df['vin_nomer'] + df["gos_nomer"]
    result = df[['guid', 'name', 'description']]
    return result
