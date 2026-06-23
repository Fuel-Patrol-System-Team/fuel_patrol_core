from typing import Any, List, cast

import polars as pl
from lark import Lark, Transformer
import logging

logger = logging.getLogger(__name__)


class GlonassExpresssionParser:
    """
    Парсит expression для датчиков
    """

    def make_variables(self, sensors_mapping: dict[str, List[dict[str, Any]]]):
        variables = {}
        for name, sensors in sensors_mapping.items():
            for sensor in sensors:
                if sensor.get("metadata") is not None:
                    if sensor["metadata"].get("pseudonym") is not None:
                        ps = sensor["metadata"].get("pseudonym") 
                        variables[ps] = sensor["value"]
                if sensor.get("value") is not None and sensor.get("value") != "":
                    variables[sensor["value"].replace("parameters.", "")] = sensor["value"]
        return variables
    
    def parse_expression(self, sensor_name: str, sensor_param: str,  expr: str, variables: dict[str, str], df: pl.DataFrame ):
        if sensor_name != "ign":
            return df
        parser = self._build_language_parser(variables)
        try:
            expression = cast(pl.Expr, parser.parse(expr) )
            copy = df.with_columns(expression.alias(sensor_param))
            return copy
        except BaseException as exception:
            logger.error("Exception when trying to parse expresssion exception")
            return df


    grammar = r"""
        ?start: expr

        ?expr: expr "AND" expr   -> and_op
            | expr "OR" expr    -> or_op
            | "NOT" expr        -> not_op
            | comparison
            | "(" expr ")"

        comparison: NAME OP value

        value: NUMBER           -> number
            | STRING           -> string

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
        
        def NAME(self, token):
            return self.variables[str(token)]

        def number(self, items):
            val = str(items[0])
            return float(val) if "." in val else int(val)

        def string(self, items):
            return str(items[0])[1:-1]  # remove quotes

        def comparison(self, items):
            col, op, val = items

            col_expr = pl.col(col)
            lit_expr = pl.lit(val)

            return {
                ">": col_expr > lit_expr,
                "<": col_expr < lit_expr,
                ">=": col_expr >= lit_expr,
                "<=": col_expr <= lit_expr,
                "==": col_expr == lit_expr,
                "!=": col_expr != lit_expr,
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