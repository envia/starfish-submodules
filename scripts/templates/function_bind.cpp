{%- if not function.name == '_unnamed_' and (not function.unimplemented or strict_mode) %}
{% import 'util.cpp' as util_macro %}
{% call util_macro.ifdef(function.flags) %}
    {% set fn_name = '%s%s'|format(function.name, name)
                      if function.custom else function.name %}
    {% set fn_static = function.static if not function.operations else function.operations[0].static %}
    {% if function.exposed == ['Window'] %}
    if (scriptBindingInstance->hasWindow()) {
    {% elif function.exposed == ['Worker'] %}
    if (!scriptBindingInstance->hasWindow()) {
    {% endif %}
    StringRef* {{ function.name }}String = StringRef::createFromASCII("{{ function.name }}");
    {% if fn_static %}
        {% set object %}{{ name }}Function{% endset %}
    {% else %}
        {% set object %}targetObject{% endset %}
    {% endif %}
    {% set writable = 'false' if function.unforgeable else 'true' %}
    {% set enumerable = 'true' %}
    {% set configurable = 'false' if function.unforgeable else 'true' %}
    {% if function.force_deny_strict %}
        {% set fnStrict %}false{% endset %}
    {% else %}
        {% set fnStrict %}true{% endset %}
    {% endif %}
    FunctionObjectRef* {{fn_name}}ESFn = FunctionObjectRef::create(state,
                            FunctionObjectRef::NativeFunctionInfo(AtomicStringRef::create(context, "{{ function.name }}"), {{ fn_name }}Function, {{ function.min_passed_count|default(0) }}, {{ fnStrict }}, false)
                            );
    {{ object }}->defineDataProperty(state,
                            ValueRef::create({{ function.name }}String),
                            ValueRef::create({{fn_name}}ESFn),
                            {{ writable }}/* writable */, {{ enumerable }}/* enumerable */, {{ configurable }}/* configurable */
                            );
    {% if function.exposed == ['Window'] or function.exposed == ['Worker'] %}
    }
    {% endif %}
{% endcall %}
{% endif %}
