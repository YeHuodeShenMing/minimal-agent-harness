import ast
import json
import math
import operator
import uuid
from dataclasses import dataclass

from .online_tools import search, weather


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    execute: object

    def spec(self):
        return {"type": "function", "function": {"name": self.name,
                "description": self.description, "parameters": self.schema}}


def validate(value, schema):
    kind = schema.get("type")
    types = {"object": dict, "string": str, "integer": int, "boolean": bool, "array": list}
    if kind in types and (not isinstance(value, types[kind]) or
                          kind == "integer" and isinstance(value, bool)):
        raise ValueError(f"expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("value outside enum")
    if isinstance(value, str) and len(value) > schema.get("maxLength", 4000):
        raise ValueError("string too long")
    if kind == "integer":
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise ValueError("integer outside bounds")
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise ValueError("missing required parameters")
        if schema.get("additionalProperties") is False and value.keys() - props.keys():
            raise ValueError("unknown parameters")
        for key, item in value.items():
            if key in props:
                validate(item, props[key])


def calculator(args, state):
    expression = args["expression"]
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 80:
        raise ValueError("expression too complex")
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in ops:
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(b) > 12:
                raise ValueError("exponent too large")
            result = ops[type(node.op)](a, b)
        else:
            raise ValueError("only numeric arithmetic is allowed")
        if isinstance(result, complex) or not math.isfinite(result) or abs(result) > 1e100:
            raise ValueError("result outside limits")
        return result
    return {"result": visit(tree.body)}


def todo(args, state):
    items = state.setdefault("todos", [])
    action = args["action"]
    if action == "list":
        offset, limit = args.get("offset", 0), args.get("limit", 5)
        page = []
        for item in items[offset:offset + limit]:
            if len(json.dumps(page + [item], ensure_ascii=False).encode()) > 10000:
                break
            page.append(item)
        result = {"items": page}
        if offset or len(items) > len(page):
            result.update(total=len(items), next_offset=offset + len(page) if offset + len(page) < len(items) else None)
        return result
    if action == "add":
        text = args.get("text", "").strip()
        if not text:
            raise ValueError("add requires nonempty text")
        if len(items) >= 100:
            raise ValueError("session todo limit (100) reached")
        item = {"id": uuid.uuid4().hex[:12], "text": text, "done": False}
        items.append(item)
        return item
    item = next((t for t in items if t["id"] == args.get("id")), None)
    if item is None:
        raise ValueError("todo id not found; list todos first")
    if action == "complete":
        item["done"] = True
    elif action == "delete":
        items.remove(item)
    return item


class Registry:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        if tool.name in self.tools:
            raise ValueError("duplicate tool")
        self.tools[tool.name] = tool

    def specs(self):
        return [t.spec() for t in self.tools.values()]

    def call(self, name, args, state):
        if name not in self.tools:
            raise ValueError("unknown tool")
        tool = self.tools[name]
        validate(args, tool.schema)
        return tool.execute(args, state)


def default_registry():
    registry = Registry()
    def schema(props, required):
        return {"type": "object", "properties": props, "required": required,
                "additionalProperties": False}
    registry.register(Tool("calculator", "Safely evaluate numeric arithmetic.",
        schema({"expression": {"type": "string", "maxLength": 300}}, ["expression"]), calculator))
    registry.register(Tool("todo", "Manage todos in this session only. List before using an unknown id. List returns up to 5 items by default; follow next_offset to see remaining items.",
        schema({"action": {"type": "string", "enum": ["add", "list", "complete", "delete"]},
                "text": {"type": "string", "maxLength": 500}, "id": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5}}, ["action"]), todo))
    registry.register(Tool("search", "Search the live web via Tavily. Returns source URLs and snippets; cite relevant URLs. Optional time_range filters recent results.",
        schema({"query": {"type": "string", "maxLength": 300},
                "time_range": {"type": "string", "enum": ["day", "week", "month", "year"]}}, ["query"]), search))
    registry.register(Tool("weather", "Get real current weather estimates and 7-day forecasts from Open-Meteo. Uses the top matching city; show its full location. For ambiguous places use 'city, region' or country_code. Optional date is YYYY-MM-DD within the next 7 days; omit it for relative dates like tomorrow: use the returned local forecast dates. Cite Open-Meteo and GeoNames.",
        schema({"city": {"type": "string", "maxLength": 100},
                "country_code": {"type": "string", "maxLength": 2},
                "date": {"type": "string", "maxLength": 10}}, ["city"]), weather))
    return registry
