"""Run with python3 -m unittest discover -s tests (requires Jinja2 and C++11)."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import starfish_code_generator as generator


# Model the engine's string conversion and Optional<String*> storage. Compile
# the actual template output so assertions observe native values, not C++ text.
RUNTIME = r'''
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>

struct String {
    std::string text;
    static String* emptyString;
    static String* fromUTF8(const char* text) {
        static std::deque<String> strings;
        strings.push_back({text});
        return &strings.back();
    }
    bool equals(const char* other) const { return text == other; }
};
String* String::emptyString = String::fromUTF8("");

template <typename T> struct Optional;
template <typename T> struct Optional<T*> {
    T* value = nullptr;
    Optional() = default;
    Optional(T* value) : value(value) {}
    T* operator->() const {
        if (!value) throw std::runtime_error("empty optional access");
        return value;
    }
};

struct State;
int conversions = 0;
struct ValueRef {
    enum Kind { Undefined, Null, Text, Object, ThrowingObject } kind;
    const char* text;
    bool isUndefined() const { return kind == Undefined; }
    bool isNull() const { return kind == Null; }
    bool isUndefinedOrNull() const { return isUndefined() || isNull(); }
    bool toBoolean(State*) const {
        ++conversions;
        return !isUndefinedOrNull() && text[0];
    }
    int32_t toInt32(State*) const {
        ++conversions;
        return isUndefinedOrNull() ? 0 : std::atoi(text);
    }
    uint32_t toUint32(State* state) const { return toInt32(state); }
    double toNumber(State*) const {
        ++conversions;
        if (isUndefined()) return std::numeric_limits<double>::quiet_NaN();
        return isNull() ? 0 : std::atof(text);
    }
};
struct State {
    void throwException(int) { throw std::runtime_error("TypeError"); }
};
struct StringRef { static int createFromASCII(const char*) { return 0; } };
struct TypeErrorObjectRef { static int create(State*, int) { return 0; } };
String* toBrowserString(State*, ValueRef* arg) {
    ++conversions;
    if (arg->kind == ValueRef::ThrowingObject)
        throw std::runtime_error("conversion exception");
    if (arg->isNull()) return String::fromUTF8("null");
    if (arg->isUndefined()) return String::fromUTF8("undefined");
    return String::fromUTF8(arg->text);
}
std::string describe(String* value) {
    return value ? "value:" + value->text : "null";
}
std::string describe(Optional<String*> value) { return describe(value.value); }
'''


def compile_and_run(code):
    with tempfile.TemporaryDirectory(prefix="binding-arguments-") as directory:
        source = Path(directory) / "test.cpp"
        binary = Path(directory) / "test"
        source.write_text("\n".join(code))
        compiler = shlex.split(os.environ.get("CXX", "c++"))
        subprocess.run(compiler + ["-std=c++11", str(source), "-o", str(binary)],
                       check=True)
        return subprocess.check_output([str(binary)], text=True).splitlines()


class EnumArgumentTest(unittest.TestCase):
    def setUp(self):
        env = Environment(loader=FileSystemLoader(str(ROOT / "scripts/templates")),
                          trim_blocks=True, lstrip_blocks=True)
        for name, function in {
            "assert_true": "filter_assert_true",
            "assert_false": "filter_assert_false",
            "to_arg_syntax": "filter_to_argument_syntax",
            "first_word_capitalize": "filter_first_word_capitalize",
            "to_h_path": "filter_to_header_path",
            "to_union_h_path": "filter_to_union_header_path",
            "digit": "filter_digit",
        }.items():
            env.filters[name] = getattr(generator, function)
        for name in ("NON_NULLABLE_TYPE_KINDS", "STRING_TYPE_KINDS",
                     "POINTER_TYPE_KINDS", "STRONG_TYPE_KINDS",
                     "NUMBER_TYPE_NAMES", "STRICT_MODE", "HAS_SEQUENCE_TYPE_KINDS"):
            env.globals[name.lower()] = getattr(generator, name)
        self.template = env.get_template("util.cpp").module

    def test_native_values(self):
        template = self.template
        cases = []
        code = [RUNTIME]
        inputs = [
            ("undefined", "Undefined", ""),
            ("null", "Null", ""),
            ("valid", "Text", "a"),
            ("empty", "Text", ""),
            ("null_string", "Text", "null"),
            ("object_valid", "Object", "a"),
            ("object_empty", "Object", ""),
            ("object_null", "Object", "null"),
            ("throwing_object", "ThrowingObject", ""),
        ]
        for nullable in (False, True):
            for allow_empty in (False, True):
                forms = [("single", None), ("default", '"a"'),
                         ("trailing", None), ("followed", None)]
                if nullable:
                    forms += [("null_default", "nullptr")]
                for form, default in forms:
                    arg = {"optional": True, "type": {
                        "kind": "Enum", "name": "TestEnum", "nullable": nullable,
                        "data": ["a"] + ([""] if allow_empty else [])}}
                    if default is not None:
                        arg["default"] = default
                    names = {"name": "Test", "aname": "arg", "vname": "value"}
                    function = "convert" + str(len(cases))
                    code.append("std::string " + function + "(ValueRef* arg) {")
                    code.append("State local; State* state = &local;")
                    code.append("size_t validArgCount = 1;")
                    if form in ("trailing", "followed"):
                        code.append("bool argCounting = " +
                                    ("false" if form == "followed" else "true") + ";")
                    code.append(template.handle_arg(
                        arg, names, need_counting=form in ("trailing", "followed")))
                    code.append('if (!validArgCount) return "omitted";')
                    code.append("return describe(value); }")
                    for label, kind, text in inputs:
                        if kind == "Undefined":
                            # A missing argument keeps the declared initial
                            # value: the default, null for a nullable enum,
                            # or the empty sentinel string; only a trailing
                            # omission is counted.
                            if default == '"a"':
                                result = "value:a"
                            elif default == "nullptr":
                                result = "null"
                            elif form == "followed":
                                result = "null" if nullable else "value:"
                            else:
                                result = "omitted"
                            count = 0
                        elif kind == "Null":
                            result = "null" if nullable else "TypeError"
                            count = 0 if nullable else 1
                        else:
                            result = ("conversion exception" if kind == "ThrowingObject"
                                      else "value:" + text if text == "a" or
                                      (allow_empty and text == "") else "TypeError")
                            count = 1
                        name = (nullable, allow_empty, form, label)
                        cases.append((name, function, kind, text, result, count))

        code.append("int main() {")
        for _, function, kind, text, _, _ in cases:
            code.append("{ ValueRef arg{ValueRef::" + kind + ", " + json.dumps(text) + "};")
            code.append("conversions = 0;")
            code.append("try { std::cout << " + function + "(&arg); }")
            code.append("catch (const std::exception& e) { std::cout << e.what(); }")
            code.append('std::cout << "\\t" << conversions << "\\n"; }')
        code.append("}")
        lines = compile_and_run(code)
        self.assertEqual(len(lines), len(cases))
        for (name, _, _, _, result, count), actual in zip(cases, lines):
            with self.subTest(nullable=name[0], empty_member=name[1],
                              form=name[2], input=name[3]):
                self.assertEqual(actual, result + "\t" + str(count))

    def test_non_enum_optional_arguments(self):
        # Until native calls can represent missing middle arguments for other
        # types, preserve their conversion instead of passing uninitialized
        # integers or silently substituting zero for an unrestricted double.
        code = [RUNTIME]
        cases = []
        for type_name in ("boolean", "long", "unsigned short", "double"):
            for form in ("single", "trailing", "followed"):
                arg = {"optional": True, "type": {
                    "kind": "PrimitiveType", "name": type_name,
                    "unrestricted": type_name == "double"}}
                names = {"name": "Test", "aname": "arg", "vname": "value"}
                function = "convert" + str(len(cases))
                code.append("std::string " + function + "(ValueRef* arg) {")
                code.append("State local; State* state = &local;")
                code.append("size_t validArgCount = 1;")
                if form != "single":
                    code.append("bool argCounting = " +
                                ("false" if form == "followed" else "true") + ";")
                code.append(self.template.handle_arg(
                    arg, names, need_counting=form != "single"))
                code.append('if (!validArgCount) return "omitted";')
                # Check that conversion ran before observing value: the
                # regression must fail without reading an uninitialized value.
                code.append('if (!conversions) return "not converted";')
                code.append('if (std::isnan(double(value))) return "nan";')
                code.append("std::ostringstream result; result << value;")
                code.append("return result.str(); }")
                for kind, text in (("Undefined", ""), ("Null", ""), ("Text", "17")):
                    if kind == "Undefined" and form != "followed":
                        expected, count = "omitted", 0
                    elif kind == "Undefined" and type_name == "double":
                        expected, count = "nan", 1
                    elif kind in ("Undefined", "Null"):
                        expected, count = "0", 1
                    else:
                        expected, count = ("1" if type_name == "boolean" else "17"), 1
                    cases.append((type_name, form, function, kind, text, expected, count))

        code.append("int main() {")
        for _, _, function, kind, text, _, _ in cases:
            code.append("{ ValueRef arg{ValueRef::" + kind + ", " + json.dumps(text) + "};")
            code.append("conversions = 0;")
            code.append("std::cout << " + function + "(&arg);")
            code.append('std::cout << "\\t" << conversions << "\\n"; }')
        code.append("}")
        lines = compile_and_run(code)
        self.assertEqual(len(lines), len(cases))
        for (type_name, form, _, kind, _, expected, count), actual in zip(cases, lines):
            with self.subTest(type=type_name, form=form, input=kind):
                self.assertEqual(actual, expected + "\t" + str(count))


if __name__ == "__main__":
    unittest.main()
