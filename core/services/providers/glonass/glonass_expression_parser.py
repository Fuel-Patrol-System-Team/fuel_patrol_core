from typing import Any, List, cast
import re

import polars as pl
from lark import Lark, Transformer
import logging

logger = logging.getLogger(__name__)

# Ключевые слова грамматики, которые не являются именами переменных
_GRAMMAR_KEYWORDS = {"AND", "OR", "NOT"}


def _extract_var_names(expr: str) -> set[str]:
    """Извлекает имена переменных из выражения, исключая ключевые слова грамматики."""
    names = set(re.findall(r"[a-zA-Z_]\w*", expr))
    return names - _GRAMMAR_KEYWORDS


class GlonassExpresssionParser:
    """
    Парсит expression для датчиков
    """

    def make_variables(
        self,
        sensors_mapping: dict[str, List[dict[str, Any]]],
        expr_result_cols: dict[str, str] | None = None,
    ):
        """Строит映射ение имён переменных → колонки DataFrame.

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
                    variables[sensor["value"].replace("parameters.", "")] = sensor["value"]
        return variables

    def parse_expression(self, sensor_name: str, sensor_param: str,  expr: str, variables: dict[str, str], df: pl.DataFrame ):
        parser = self._build_language_parser(variables)
        try:
            expression = cast(pl.Expr, parser.parse(expr) )
            copy = df.with_columns(expression.alias(sensor_param))
            return copy
        except BaseException as exception:
            logger.error(f"Exception when trying to parse expresssion: {exception!r} expr={expr!r}")
            return df


    grammar = r"""
        ?start: expr

        ?expr: expr "AND" expr   -> and_op
            | expr "OR" expr    -> or_op
            | "NOT" expr        -> not_op
            | comparison
            | arith

        comparison: arith OP arith

        ?arith: arith "+" term   -> add_op
            | arith "-" term   -> sub_op
            | term

        ?term: term "*" factor   -> mul_op
            | term "/" factor   -> div_op
            | factor

        ?factor: NAME           -> var
            | NUMBER           -> number
            | STRING           -> string
            | "-" factor       -> neg_op
            | "(" expr ")"

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



    def _build_language_parser(self, variables: dict[str, str]):
        parser = Lark(self.grammar, parser="lalr", transformer=self.ToPolars(variables))
        return parser