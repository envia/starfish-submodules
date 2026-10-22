#!/usr/bin/env python3
"""Compile enum conversions and exercise values, defaults, and absence."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jinja2 import Environment, FileSystemLoader

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from starfish_code_generator import (
    NON_NULLABLE_TYPE_KINDS, STRING_TYPE_KINDS, POINTER_TYPE_KINDS,
    NUMBER_TYPE_NAMES, STRONG_TYPE_KINDS,
)

STUBS = r"""
#include <stdexcept>
#include <string>
struct String {
    std::string text;
    bool equals(const char* other) { return text == other; }
    static String* fromUTF8(const char* text) { return new String{text}; }
    static String* emptyString;
};
String empty{""};
String* String::emptyString = &empty;
template<class T> struct Optional {
    T pointer = nullptr;
    Optional& operator=(T value) { pointer = value; return *this; }
    T operator->() {
        if (!pointer) throw std::logic_error("dereferenced null enum");
        return pointer;
    }
};
struct Value {
    enum Kind { Undefined, Null, Text } kind;
    const char* text;
    bool isUndefined() { return kind == Undefined; }
    bool isNull() { return kind == Null; }
    bool isUndefinedOrNull() { return isUndefined() || isNull(); }
};
struct State { void throwException(int) { throw std::invalid_argument("enum"); } };
struct StringRef { static int createFromASCII(const char*) { return 0; } };
struct TypeErrorObjectRef { static int create(State*, int) { return 0; } };
String* toBrowserString(State*, Value* value) {
    return String::fromUTF8(value->isUndefined() ? "undefined" :
                           value->isNull() ? "null" : value->text);
}
std::string text(String* value) { return value->text; }
std::string text(Optional<String*> value) {
    return value.pointer ? value.pointer->text : "<null>";
}
bool check(std::string (*convert)(Value*), Value value, const char* expected) {
    try { return convert(&value) == expected; }
    catch (const std::invalid_argument&) { return std::string(expected) == "<error>"; }
}
"""


class EnumConversion(unittest.TestCase):
    def test_generated_conversions(self):
        env = Environment(loader=FileSystemLoader(str(SCRIPTS / "templates")))
        env.globals.update(
            non_nullable_type_kinds=NON_NULLABLE_TYPE_KINDS,
            string_type_kinds=STRING_TYPE_KINDS,
            pointer_type_kinds=POINTER_TYPE_KINDS,
            number_type_names=NUMBER_TYPE_NAMES,
            strong_type_kinds=STRONG_TYPE_KINDS,
        )
        macro = env.get_template("util.cpp").module.handle_arg
        source = STUBS
        checks = []
        # Inputs: omitted/undefined, null, empty string, valid value, invalid value.
        inputs = ('{Value::Undefined, ""}', '{Value::Null, ""}',
                  '{Value::Text, ""}', '{Value::Text, "classic"}',
                  '{Value::Text, "unknown"}')
        cases = [
            ("required", False, False, None, False, False,
             ["<error>", "<error>", "<error>", "classic", "<error>"]),
            ("optional", True, False, None, False, False,
             ["", "<error>", "<error>", "classic", "<error>"]),
            ("member", False, True, None, False, False,
             ["", "<error>", "<error>", "classic", "<error>"]),
            ("defaulted", False, True, '"classic"', False, False,
             ["classic", "<error>", "<error>", "classic", "<error>"]),
            ("empty_allowed", False, True, '""', False, True,
             ["", "<error>", "", "classic", "<error>"]),
            ("nullable", False, True, None, True, False,
             ["<null>", "<null>", "<error>", "classic", "<error>"]),
            ("null_default", False, True, 'nullptr', True, False,
             ["<null>", "<null>", "<error>", "classic", "<error>"]),
        ]
        for name, optional, member, default, nullable, empty_allowed, expected in cases:
            values = ["classic", "module"] + ([""] if empty_allowed else [])
            arg = dict(optional=optional, required=not (optional or member),
                       default=default, treat_null_as=None,
                       type=dict(kind="Enum", name="Example", data=values,
                                 nullable=nullable))
            names = dict(aname="arg", vname="value", kname="member" if member else None)
            generated = macro(arg, names)
            source += (f"\nstd::string {name}(Value* arg) {{\n"
                       "State instance; auto* state = &instance; int validArgCount = 1;\n"
                       + generated + "\nreturn text(value);\n}\n")
            for value, result in zip(inputs, expected):
                checks.append(f'if (!check({name}, {value}, "{result}")) return {len(checks) + 1};')
        source += "\nint main() {\n" + "\n".join(checks) + "\n}\n"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cpp = directory / "test.cpp"
            cpp.write_text(source)
            executable = directory / "test"
            subprocess.run([os.environ.get("CXX", "c++"), "-std=c++11",
                            str(cpp), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
