#!/usr/bin/env python3

import json
import os
import re
import shutil
import subprocess
from glob import glob

import jinja2
from lxml import etree

# Regular expressions
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
    class_ = mutation["class"]
    method = mutation["method"]
    signature = mutation["description"]
    return f"""<code>{class_}.{method}{signature}</code>"""


def trim_package(s: str):
    match = re.search(r"\w+\.\w+\(.*$", s)
    return f"""<code>{match.group()}</code>"""


def test_case_link(test_case):
    return f"""<code>{test_case}</code>"""


def location_link(location):
    path = location["file"]
    return f"""<code>{os.path.basename(path)}</code>"""


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
env.filters["trim_package"] = trim_package
env.filters["test_case_link"] = test_case_link
env.filters["location_link"] = location_link
env.filters["describe"] = describe
env.filters["ordinal"] = ordinal

template = env.get_template("report_fr.html.j2")


# Main
def main():
    run_reneri()
    test_cases = load_test_cases("target/mutations.json")
    method_locations = load_method_locations("target/mutations.xml")
    hints = []
    for hint_file in find_hints():
        hint_folder = os.path.dirname(hint_file)
        hints.extend(get_hints(hint_folder, test_cases, method_locations))
    report = generate_readable_report(hints)
    with open("report.html", "w") as file:
        file.write(report)


def run_reneri():
    subprocess.run(["mvn",
        "test-compile",
        "org.pitest:pitest-maven:mutationCoverage"])

    report_name = sorted(os.listdir("target/pit-reports"))[-1]
    report_path = os.path.join("target/pit-reports", report_name)
    for name in ["methods.json", "mutations.json", "mutations.xml"]:
        shutil.copy2(os.path.join(report_path, name), "target")

    subprocess.run(["mvn",
        "eu.stamp-project:reneri:observeMethods",
        "eu.stamp-project:reneri:observeTests",
        "eu.stamp-project:reneri:hints"])


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
            match = re.search(r"\.(?P<test>\w+\.\w+)\(", test)
            tests.append(match.group("test"))
    return result


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


def generate_readable_report(hints):
    return template.render(hints=hints)


def get_hints(hint_folder, test_cases, method_locations):
    with open(os.path.join(hint_folder, "mutation.json"), "r") as file:
        mutation_data = json.load(file)
    with open(os.path.join(hint_folder, "hints.json"), "r") as file:
        hint_data = json.load(file)

    if not isinstance(hint_data, list):
        hint_data = [hint_data]
    
    diffs = load_diffs(hint_folder)
    diffs_list = list(diffs.values())

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
                "class": mutation_data['class'],
                "full_class_name": f"{mutation_data['package']}.{mutation_data['class']}",
                "method": mutation_data["method"],
                "description": mutation_data["description"],
                "signature": mutation_data["description"],
                "is_void": is_void(mutation_data["description"]),
                "tests": test_cases[mutation_id2(mutation_data)],
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


def method_name(entry):
    class_ = entry["class"]
    method = entry["method"]
    signature = entry["desc"]
    return f"{class_}.{method}{signature}"


def mutation_id2(mutation):
    package = mutation["package"]
    class_ = mutation["class"]
    method = mutation["method"]
    signature = mutation["description"]
    mutator = mutation["mutator"]
    return f"{package}.{class_}.{method}{signature}{mutator}"


if __name__ == "__main__":
    main()
