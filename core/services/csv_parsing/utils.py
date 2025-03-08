from pathlib import Path
import pandas as pd
import numpy as np
import warnings
## TODO: (YShipik) - блоки кода из тасков перенеси сюда, почему то когда вызываю в функциях - у меня вечная ошика, я напрямую писал в тасках
def parse_cars(path: Path) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore')
        cars_df = pd.read_csv(
            path,
            usecols=['guid', 'gos_nomer', 'vin_nomer', 'marka', 'model', 'description', 'sl_tip_dvigat'],
            encoding='utf-8',
            on_bad_lines='skip',
            engine='python'
            )
        cars_df['marka'].replace(np.nan, "", inplace=True)
        cars_df['model'].replace(np.nan, "", inplace=True)
        cars_df['vin_nomer'].replace(np.nan, "", inplace=True)
        cars_df['gos_nomer'].replace(np.nan, "", inplace=True)
        cars_df['sl_tip_dvigat'].replace(np.nan, 0, inplace=True)
        cars_df['name'] = cars_df['marka'] + " " + cars_df['model'] + " " + cars_df['vin_nomer'] + cars_df['gos_nomer']
        parsed_cars = cars_df[['guid', 'name', 'description', 'sl_tip_dvigat']]
        return parsed_cars

NORM_SUMMER = 'Норма на 100 км (Норма за час для ТС по моточасам) Летняя'
NORM_WINTER = 'Норма на 100 км (Норма за час для ТС по моточасам) Зимняя'

PARSE_NORMS_REQUIRED_COLUMNS = ['sl_avto', 'vid_norm_rasx', 'period', 'deystvuet_do' ]
PARSE_NORMS_OUTPUT_COLUMNS = ['auto', 'winter_norm', 'summer_norm', 'due']
def parse_norms(path: Path) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore')
        norms = pd.read_csv(path)
        try:
            norms = norms.groupby(['sl_avto', 'vid_norm_rasx']).last().reset_index()
            norms['vid_norm_rasx'].unique()
            norms = norms[norms['vid_norm_rasx'].isin([NORM_SUMMER, NORM_WINTER])]
            t = {}
            for _, row in norms.iterrows():
                if row[0] not in t:
                    t[row[0]] = [ -1, -1, "2000-12-31 21:00:00"]
                
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
            return result
        except KeyError:
            diff = list( set(PARSE_NORMS_REQUIRED_COLUMNS).difference(norms.columns))
            raise KeyError(f"Не найдены колонки {diff}")
