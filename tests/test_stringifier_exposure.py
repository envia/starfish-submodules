#!/usr/bin/env python3
"""Compile stringifier installation code and check Window/Worker exposure."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jinja2 import Environment, FileSystemLoader

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from starfish_idl_reader import gen_ir_from_file

STUBS = r"""
struct ScriptBindingInstance {
    bool window;
    bool hasWindow() { return window; }
};
struct StringRef {
    static StringRef* createFromASCII(const char*) { return nullptr; }
};
struct AtomicStringRef {
    static void* create(void*, const char*) { return nullptr; }
};
int created = 0, installed = 0;
void toStringFunction() {}
struct FunctionObjectRef {
    struct NativeFunctionInfo {
        NativeFunctionInfo(void*, void (*)(), int, bool, bool) {}
    };
    static FunctionObjectRef* create(void*, NativeFunctionInfo) {
        ++created;
        return nullptr;
    }
};
struct ValueRef {
    template<class T> static void* create(T) { return nullptr; }
};
struct Object {
    void defineDataProperty(void*, void*, void*, bool, bool, bool) {
        ++installed;
    }
};
"""


class StringifierExposure(unittest.TestCase):
    def test_generated_installation(self):
        env = Environment(loader=FileSystemLoader(str(SCRIPTS / "templates")))
        template = env.get_template("function_bind.cpp")
        for exposed in (None, "Window", "Worker", "(Window,Worker)"):
            with self.subTest(exposed=exposed), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                attribute = "" if exposed is None else f"[Exposed={exposed}] "
                idl = directory / "Example.idl"
                idl.write_text("[Exposed=(Window,Worker)] interface Example {\n"
                               + attribute + "stringifier;\n};\n")
                interface = gen_ir_from_file(str(idl))["interfaces"]["Example"]
                function = interface["functions"][0]
                generated = template.render(function=function, name="Example",
                                            strict_mode=False)
                expected = "true" if exposed in (None, "(Window,Worker)") else (
                    "window" if exposed == "Window" else "!window")
                source = STUBS + """
void install(bool window) {
    ScriptBindingInstance instance{window};
    auto* scriptBindingInstance = &instance;
    void* state = nullptr;
    void* context = nullptr;
    Object object;
    auto* targetObject = &object;
""" + generated + """
}
int main() {
    for (bool window : {false, true}) {
        created = installed = 0;
        install(window);
        int expected = """ + expected + """;
        if (created != expected || installed != expected) return 1;
    }
}
"""
                cpp = directory / "test.cpp"
                cpp.write_text("#include <initializer_list>\n" + source)
                executable = directory / "test"
                subprocess.run([os.environ.get("CXX", "c++"), "-std=c++11",
                                str(cpp), "-o", str(executable)], check=True)
                subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
