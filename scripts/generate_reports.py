#!/usr/bin/env python3

import json
import os
import re
from glob import glob

import jinja2
from lxml import etree

# Regular expressions
TEST_CASE_PATTERN = re.compile(
    r"^(?P<class>.+)\.(?P<method>[^\[]+)((?P<params>\[.*\])?\((?P=class)?\))?$")
TEST_CLASS_PATTERN = re.compile(
    r"^(?P<class>.+)\.(?P=class)$")
POINTCUT_PATTERN = re.compile(r"""
    (?P<class>[^\|]+)\|
    (?P<method>[^\|]+)\|
    (
        ((?P<description>[^\|]+)\|(?P<invocation>\d+)\|\#((?P<result>result)|(?P<that>that)|(?P<argument>\d+)))
        |
        ((?P<expression>\d+)|(?P<exception>!))
    )
    (\|(?P<field>[^\#\|]+))?
    (\|\#((?P<size>size)|(?P<length>length)))?""", re.VERBOSE)


# Templating
def method_link(mutation):
    class_ = mutation["full_class_name"]
    method = mutation["method"]
    signature = mutation["description"]
    return f"""<a href="#">{class_}.{method}{signature}</a>"""


def test_case_link(test_case):
    class_ = test_case["class"]
    method = test_case.get("method")
    if method:
        name = f"{class_}.{method}"
    else:
        name = class_
    return f"""<a href="#">{name}</a>"""


def location_link(location):
    path = location["file"]
    return f"""<a href="#"><code>{os.path.basename(path)}</code></a>"""


def describe(value):
    if "exceptionMessage" in value:
        return f"""an exception of type <code>{value["typeName"]}</code> with message <code>{value["exceptionMessage"]}</code>"""
    elif "literalValue" in value:
        return f"""<code>{value["literalValue"]}</code>"""
    elif "isNull" in value:
        return "null" if value["isNull"] else "non-null"
    raise ValueError("Should be one value or another")


def ordinal(value):
    SUFFIXES = ['th'] + ['st', 'nd', 'rd'] + 6 * ['th']
    return f"""{value}{SUFFIXES[value % 10]}"""


env = jinja2.Environment(
    loader=jinja2.loaders.PackageLoader("generate_reports"),
    autoescape=jinja2.select_autoescape(),
    trim_blocks=True,
    lstrip_blocks=True,
)
env.filters["method_link"] = method_link
env.filters["test_case_link"] = test_case_link
env.filters["location_link"] = location_link
env.filters["describe"] = describe

template = env.get_template("report_en.html.j2")


# Main
def main():
    print("""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rapport</title></head><body>""")
    test_cases = load_test_cases("target/mutations.json")
    method_locations = load_method_locations("target/mutations.xml")
    for hint_file in find_hints():
        hint_folder = os.path.dirname(hint_file)
        print(generate_readable_report(hint_folder, test_cases, method_locations))
        print("""<hr>""")
    print("""</body></html>""")


def load_test_cases(path):
    with open(path, "r") as file:
        data = json.load(file)
    result = {}
    for mutation in data["mutations"]:
        if mutation["status"] != "SURVIVED":
            continue
        tests = []
        result[mutation_id1(mutation)] = tests
        for test in mutation["tests"]["ordered"]:
            match = match_test_case(test)
            if not match:
                tests.append({"class": test})
            else:
                tests.append({
                    "class": match.group("class"),
                    "method": match.group("method"),
                })
    return result


def match_test_case(s):
    return TEST_CLASS_PATTERN.match(s) or TEST_CASE_PATTERN.match(s)


def mutation_id1(mutation):
    package = mutation["method"]["package"]
    class_ = mutation["method"]["class"]
    method = mutation["method"]["name"]
    signature = mutation["method"]["description"]
    mutator = mutation["mutator"]
    return f"{package}.{class_}.{method}{signature}{mutator}"


def load_method_locations(path):
    with open(path, "r") as file:
        data = etree.parse(file)
    result = {}
    for mutation in data.findall("mutation"):
        if mutation.get("status") != "SURVIVED":
            continue
        class_ = mutation.findtext("mutatedClass")
        method = mutation.findtext("mutatedMethod")
        description = mutation.findtext("methodDescription")
        line = int(mutation.findtext("lineNumber"))
        result[f"{class_}.{method}{description}"] = line
    return result


def find_hints():
    a = glob("target/reneri/observations/methods/*/*/hints.json")
    b = glob("target/reneri/observations/tests/*/hints.json")
    return a + b


def generate_readable_report(hint_folder, test_cases, method_locations):
    for info in get_hints(hint_folder, test_cases, method_locations):
        return template.render(info)


def get_hints(hint_folder, test_cases, method_locations):
    with open(os.path.join(hint_folder, "mutation.json"), "r") as file:
        mutation_data = json.load(file)
    with open(os.path.join(hint_folder, "hints.json"), "r") as file:
        hint_data = json.load(file)
    diffs = load_diffs(hint_folder)
    diffs_list = list(diffs.values())

    if not isinstance(hint_data, list):
        hint_data = [hint_data]

    def method_name(entry):
        class_ = entry["class"]
        method = entry["method"]
        signature = entry["desc"]
        return f"{class_}.{method}{signature}"
    
    for item in hint_data:
        type_ = item["hint-type"]
        accessors = item.get("accessors")
        pointcut = item.get("pointcut")

        hint = {"type": type_}
        if type_ == "infection":
            hint["targets"] = [method_name(entry) for entry in item["entry-points"]]
            hint["direct_access"] = mutated_method_is_accessible(mutation_data, hint["targets"])
        if type_ == "observation":
            hint["location"] = item["location"]
        if accessors is not None:
            hint["targets"] = [method_name(entry) for entry in accessors]

        concrete_diff = None
        if pointcut is not None:
            concrete_diff = diffs.get(pointcut, None)
        if diffs_list:
            concrete_diff = diffs_list[0]
        
        yield {
            "mutation": {
                "mutator": mutation_data["mutator"],
                "full_class_name": f"{mutation_data['package']}.{mutation_data['class']}",
                "method": mutation_data["method"],
                "description": mutation_data["description"],
                "signature": mutation_data["description"],
                "is_void": is_void(mutation_data["description"]),
                "tests": test_cases.get(mutation_id2(mutation_data), mutation_data["tests"]),
                "line": method_locations.get(f"{mutation_data['package']}.{mutation_data['class']}.{mutation_data['method']}{mutation_data['description']}", None)
            },
            "hint": hint,
            "diff": concrete_diff,
        }


def load_diffs(hint_folder):
    try:
        with open(os.path.join(hint_folder, "diff.json"), "r") as file:
            data = json.load(file)
    except FileNotFoundError:
        return {}
    result = {}
    for entry in data:
        if len(entry["expected"]) != 1:
            continue
        result[entry["pointcut"]] = {
            "pointcut": POINTCUT_PATTERN.match(entry["pointcut"]).groupdict(),
            "expected": entry["expected"][0],
            "observed": entry["unexpected"][0],
        }
    return result


def mutated_method_is_accessible(mutation_data, targets):
    package = mutation_data["package"]
    class_ = mutation_data["class"]
    method = mutation_data["method"]
    signature = mutation_data["description"]
    return f"{package}.{class_}.{method}{signature}" in targets


def is_void(signature):
    return signature.endswith("V")


def mutation_id2(mutation):
    package = mutation["package"]
    class_ = mutation["class"]
    method = mutation["method"]
    signature = mutation["description"]
    mutator = mutation["mutator"]
    return f"{package}.{class_}.{method}{signature}{mutator}"


if __name__ == "__main__":
    main()
