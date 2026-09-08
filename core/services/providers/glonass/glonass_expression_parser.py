from typing import Any, List, cast
import re

import polars as pl
from lark import Lark, Transformer
import logging

logger = logging.getLogger(__name__)

# Ключевые слова грамматики, которые не являются именами переменных.
# Хранятся в нижнем регистре, т.к. грамматика регистронезависима для логических
# операторов (AND/OR/NOT могут быть записаны в любом регистре).
_GRAMMAR_KEYWORDS = {"and", "or", "not"}

# Поддерживаемые функции (в нижнем регистре). Используются для исключения
# имён функций из списка переменных, а также для диспетчеризации в трансформере.
_GRAMMAR_FUNCTIONS = {"if", "coalesce", "prev"}


def _extract_var_names(expr: str) -> set[str]:
    """Извлекает имена переменных из выражения, исключая ключевые слова грамматики и имена функций."""
    names = set(re.findall(r"[a-zA-Z_]\w*", expr))
    return names - _GRAMMAR_KEYWORDS - _GRAMMAR_FUNCTIONS


class GlonassExpresssionParser:
    """
    Парсит expression для датчиков
    """

    def make_variables(
        self,
        sensors_mapping: dict[str, List[dict[str, Any]]],
        expr_result_cols: dict[str, str] | None = None,
    ):
        """Определение имён переменных для выражений → колонки DataFrame.

        Parameters
        ----------
        sensors_mapping
            Маппинг сенсоров машины.
        expr_result_cols
            Словарь ``sensor_key → intermediate_column`` для сенсоров, у которых
            есть expression. Если передан, то псевдонимы (pseudonym) таких
            сенсоров будут указывать на промежуточную колонку с вычисленным
            результатом, а не на сырую колонку. Имена сырых колонок всегда
            указывают на сырые данные.
        """
        variables = {}
        for name, sensors in sensors_mapping.items():
            for sensor in sensors:
                if sensor.get("metadata") is not None:
                    if sensor["metadata"].get("pseudonym") is not None:
                        ps = sensor["metadata"].get("pseudonym")
                        # Если у сенсора есть expression, псевдоним должен
                        # указывать на промежуточную колонку с результатом.
                        if expr_result_cols and name in expr_result_cols:
                            variables[ps] = expr_result_cols[name]
                        else:
                            variables[ps] = sensor["value"]
                if sensor.get("value") is not None and sensor.get("value") != "":
                    raw = sensor["value"]
                    short = raw.replace("parameters.", "")
                    variables[short] = raw
                    # Glonass допускает обращение к flex_adcN по короткому имени
                    # adcN в выражениях (напр. `adc3` вместо `flex_adc3`). Если
                    # сырая колонка — `flex_adcN`, регистрируем алиас `adcN`,
                    # чтобы такие ссылки разрешались в реальную колонку. Алиас
                    # не перетирает уже зарегистрированное имя (явный сенсор
                    # `adcN` имеет приоритет), а также не перетирается сам, т.к.
                    # добавляется до обработки последующих сенсоров.
                    flex_match = re.search(r"^flex_adc(\d+)$", short)
                    if flex_match is not None:
                        alias = f"adc{flex_match.group(1)}"
                        if alias not in variables:
                            variables[alias] = raw
        return variables

    def parse_expression(self, sensor_name: str, sensor_param: str,  expr: str, variables: dict[str, str], df: pl.DataFrame ):
        parser = self._build_language_parser(variables)
        try:
            # Guardrail: для каждой переменной, упомянутой в выражении, разрешаем
            # целевую колонку (через variables, иначе — само имя) и если её нет в
            # DataFrame, добавляем временную null-колонку, чтобы выражение
            # вычислялось, а не падало с ColumnNotFoundError. Отсутствующие
            # параметры (напр. adcN / flex_adcN, которых нет в терминальных
            # данных) дают null, корректно обрабатываемый coalesce/if/prev.
            # Временные null-колонки удаляются после вычисления — схема
            # возвращаемого DataFrame остаётся прежней (+ только sensor_param).
            missing_pairs: list[tuple[str, str]] = []
            seen: set[str] = set()
            for v in _extract_var_names(expr):
                col = variables.get(v, v)
                if col not in df.columns and col not in seen:
                    seen.add(col)
                    missing_pairs.append((v, col))
            work_df = df
            if missing_pairs:
                logger.warning(
                    "parse_expression: отсутствуют колонки для выражения "
                    "sensor=%r param=%r expr=%r — добавлены null-колонки: %s",
                    sensor_name, sensor_param, expr,
                    ", ".join(f"{v}->{c}" for v, c in missing_pairs),
                )
                work_df = df.with_columns([pl.lit(None).alias(c) for _, c in missing_pairs])
            expression = cast(pl.Expr, parser.parse(expr) )
            result = work_df.with_columns(expression.alias(sensor_param))
            if missing_pairs:
                result = result.drop([c for _, c in missing_pairs])
            return result
        except BaseException as exception:
            logger.error(f"Exception when trying to parse expresssion: {exception!r} expr={expr!r}")
            return df


    grammar = r"""
        ?start: expr

        ?expr: expr _AND expr   -> and_op
            | expr _OR expr    -> or_op
            | _NOT expr        -> not_op
            | comparison
            | arith

        comparison: arith OP arith

        ?arith: arith "+" term   -> add_op
            | arith "-" term   -> sub_op
            | term

        ?term: term "*" factor   -> mul_op
            | term "/" factor   -> div_op
            | factor

        ?factor: func
            | NAME           -> var
            | NUMBER           -> number
            | STRING           -> string
            | "-" factor       -> neg_op
            | "(" expr ")"

        func: NAME "(" [args] ")"

        args: expr ("," expr)*

        // Логические операторы регистронезависимы (AND/and/And и т.д.).
        // Классы символов вместо встроенного флага (?i), т.к. lark объединяет
        // все терминалы-regex в один шаблон, где inline-флаги ломают компиляцию.
        // Приоритет выше, чем у NAME, чтобы `and`/`or`/`not` распознавались
        // как операторы, а не как имена переменных (при равной длине лексемы).
        // Префикс `_` фильтрует терминал из дерева разбора: lark не передаёт
        // такие токены в трансформер, поэтому and_op/or_op/not_op получают
        // только операнды (как это было со строковыми литералами ранее).
        _AND.10: /[Aa][Nn][Dd]/
        _OR.10:  /[Oo][Rr]/
        _NOT.10: /[Nn][Oo][Tt]/

        NAME: /[a-zA-Z_]\w*/
        OP: ">" | "<" | ">=" | "<=" | "==" | "!="
        NUMBER: /\d+(\.\d+)?/
        STRING: /'[^']*'/

        %ignore " "
        """

    class ToPolars(Transformer):
        variables = {}
        def __init__(self,  variables: dict[str, str], visit_tokens: bool = True) -> None:
            super().__init__(visit_tokens)
            self.variables = variables
        
        def var(self, items):
            name = str(items[0])
            col = self.variables.get(name, name)
            return pl.col(col)

        def number(self, items):
            val = str(items[0])
            return pl.lit(float(val) if "." in val else int(val))

        def string(self, items):
            return pl.lit(str(items[0])[1:-1])  # remove quotes

        def neg_op(self, items):
            return -items[0]

        def add_op(self, items):
            return items[0] + items[1]

        def sub_op(self, items):
            return items[0] - items[1]

        def mul_op(self, items):
            return items[0] * items[1]

        def div_op(self, items):
            return items[0] / items[1]

        def comparison(self, items):
            left, op, right = items

            return {
                ">": left > right,
                "<": left < right,
                ">=": left >= right,
                "<=": left <= right,
                "==": left == right,
                "!=": left != right,
            }[str(op)]

        def and_op(self, items):
            return items[0] & items[1]

        def or_op(self, items):
            return items[0] | items[1]

        def not_op(self, items):
            return ~items[0]

        def args(self, items):
            """Список аргументов функции (литерал-запятые отфильтровываются lark)."""
            return list(items)

        def func(self, items):
            """Диспетчеризация вызова функции по имени (регистронезависимо)."""
            name = str(items[0]).lower()
            args = items[1] if len(items) > 1 else []

            if name == "if":
                if len(args) != 3:
                    raise ValueError(
                        f"if() expects exactly 3 arguments (cond, then, else), got {len(args)}"
                    )
                cond, then, otherwise = args
                return pl.when(cond).then(then).otherwise(otherwise)

            if name == "coalesce":
                if not args:
                    raise ValueError("coalesce() expects at least one argument")
                return pl.coalesce(args)

            if name == "prev":
                if len(args) != 1:
                    raise ValueError(
                        f"prev() expects exactly 1 argument (column), got {len(args)}"
                    )
                # Значение колонки из предыдущей строки (shift на -1):
                # для первой строки возвращает null, что корректно
                # распространяется через if/and в otherwise().
                return args[0].shift(-1)

            raise ValueError(f"Unknown function: {name!r}")



    def _build_language_parser(self, variables: dict[str, str]):
        parser = Lark(self.grammar, parser="lalr", transformer=self.ToPolars(variables))
        return parser