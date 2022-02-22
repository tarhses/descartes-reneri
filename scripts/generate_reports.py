import json
import re
from argparse import ArgumentParser
from glob import glob
from os.path import basename, dirname, exists, isdir, join

from jinja2 import Environment
from lxml import etree
from parsy import alt, regex, seq, string


def create_description_parser():

    def joining(separator):
        return lambda *args: separator.join(args)

    JVM_PRIMITIVE_TYPES = {
        'B': "byte",
        'C': "char",
        'D': "double",
        'F': "float",
        'I': "int",
        'J': "long",
        'S': "short",
        'Z': "boolean",
    }

    QUALIFIED_NAME = regex(
        r'[^\.;\[/\(\):]+').sep_by(string('/')).combine(joining('.'))
    PRIMITIVE_TYPE = regex('[BCDFIJSZ]').map(lambda i: JVM_PRIMITIVE_TYPES[i])
    CLASS_NAME = string('L') >> QUALIFIED_NAME << string(';')
    ARRAY = seq(string('[').at_least(1).map(lambda o: '[]' * len(o)),
                (PRIMITIVE_TYPE | CLASS_NAME)).combine(lambda o, t: t + o)
    TYPE = PRIMITIVE_TYPE | CLASS_NAME | ARRAY
    RETURN_TYPE = string('V').map(lambda _: 'void') | TYPE
    DESCRIPTION = seq(string('(') >> TYPE.many().combine(joining(',')), string(
        ')') >> RETURN_TYPE).combine(lambda params, rt: ('(' + params + ')', rt))

    return DESCRIPTION


DESCRIPTION_PARSER = create_description_parser()

REPORT_TEMPLATE = '''
The body of the method {{ mutation | method_link }}
was {% if mutation.mutator == 'void' %} removed {% else %} replaced by {% if mutation.mutator == 'empty'%}  a single `return` producing an empty array {% else %} `return {{ mutation.mutator }};` {% endif %} {% endif %}
yet, {% if mutation.tests|length == 1 %} {{ mutation.tests[0] | test_case_link }} did not fail. {% else %} none of the following tests failed:
{% for test in mutation.tests %}
*  {{test | test_case_link }}{% endfor %}
{% endif %}
{% if hint.type == 'execution' %}
When the transformed method is executed, there is no difference with the execution using the original source code.
{% if mutation.is_void %}
This could mean that the method is not producing any side effect.
Consider creating a modified variant of the {% if mutation.tests|length == 1 %} test mentioned {% else %} tests listed {% endif %} above where the side effects can be observed.
{% else %}
This could mean that the original method always returns the same value.
Consider creating a modified variant of the {% if mutation.tests|length == 1 %} test mentioned {% else %} tests listed {% endif %} above to make the method produce a different value.
{% endif %}
{% elif hint.type == 'observation' %}
{% set location = hint.location %}
It is possible to observe a difference between the program state when the transformed method is executed and the program state when the original method is executed.
This difference can be observed in {{ location | location_link }} from the expression returning a value of type `{{location.type}}` located {% if location.from.line == location.to.line %} in line {{location.from.line}} from column {{location.from.column}} to column {{location.to.column}} {% else %} from line {{location.from.line}} and column {{location.from.column}} to line {{location.to.line}}.
{% endif %}
{% if diff %}
{% set pointcut = diff.pointcut %}
When the transformation is applied to the method, it was observed that {% if pointcut.exception %} the exception thrown in the test {% else %} {% if pointcut.size %}the size of {% elif pointcut.length %}the length of {% endif %}{% if pointcut.field %}the field `{{pointcut.field }}` of {% endif %}{% endif %} the value obtained from the expression was {{ diff.observed|describe }} but should have been {{ diff.expected|describe }}.
{% if pointcut.field and not hint|attr("targets") %}
Consider modifying the test to verify the value of `{{pointcut.field}}` in the result of the expression.
{% endif %}
{% if hint.targets %}
Consider verifying the result or side effects of one of the following methods invoked for the result of the expression:
{% for method in hint.targets %}
- `{{ method }}`
{% endfor %}
{% endif %}
{% endif %}
{% else %}
It is possible to observe a difference between the program state when the transformed method is executed and the program state when the original method is executed. This difference is observed right after the method invocation but not from from the top level code of any test.
{% if diff %}
{% set pointcut = diff.pointcut %}
For one invocation of `{{ mutation.method }}`, it was observed that {% if pointcut.size %}the size of {% elif pointcut.length %}the length of {% endif %}{% if pointcut.field %}the field `{{pointcut.field }}` of {% endif %}{% if pointcut.result %}the return value {% elif pointcut.that %}the instance in which the method was called {% else %}{{ pointcut.argument + 1 | ordinal }} argument {% endif %} was {{ diff.observed|describe }} but should have been {{ diff.expected|describe }}.
{% endif %}
To solve this problem you may consider to:
{% if hint.direct_access %}
* Create a new test case that targets the result of `{{ mutation.method }}` directly, since it could be accessed from a test class.
{% else %}
* Create a new test case that targets the result of one of the following methods:
    {% for method in hint.targets %}
    - {{ method }}
    {% endfor %}
    these are the closest accessible methods that can be used to trigger the execution of `{{mutation.method}}`
{% endif %}
{% if not hint.direct_access %}
* Check if the effects of the method are visible from outside the class. Otherwise you may consider to add a visible method (maybe a getter) that could be used to observe the effects of `{{mutation.method}}`.
{% endif %}
* Refactor the code that uses this method. Maybe the method is not actually needed in the context that it is being used.
{% endif %}
___
'''

POINTCUT_PATTERN = re.compile(r"""
    (?P<class>[^\|]+)\|
    (?P<method>[^\|]+)\|
    (
        ((?P<description>[^\|]+)\|(?P<invocation>\d+)\|\#((?P<result>result)|(?P<that>that)|(?P<argument>\d+)))
        |
        ((?P<expression>\d+)|(?P<exception>!))
    )
    (\|(?P<field>[^\#\|]+))?
    (\|\#((?P<size>size)|(?P<length>length)))?

""", re.VERBOSE)

TEST_CASE_PATTERN = re.compile(
    r"^(?P<class>.+)\.(?P<method>[^\[]+)((?P<params>\[.*\])?\((?P=class)?\))?$")
TEST_CLASS_PATTERN = re.compile(r"^(?P<class>.+)\.(?P=class)$")


def match_test_case(test_case):
    return TEST_CLASS_PATTERN.match(test_case) or TEST_CASE_PATTERN.match(test_case)


SUFFIXES = ['th'] + ['st', 'nd', 'rd'] + 6 * ['th']


def load_json(path):
    with open(path) as _file:
        return json.load(_file)


def mutated_method_is_accessible(mutation_data, targets):
    return f'{mutation_data["package"]}.{mutation_data["class"]}.{mutation_data["method"]}{signature(mutation_data["description"])}' in targets

# def pick_a_diff(hint_folder):
#     diff_file_path = join(hint_folder, "diff.json")
#     if not exists(diff_file_path):
#         return None
#     diff_data = load_json(diff_file_path)
#     for entry in diff_data:
#         if len(entry['expected']) == 1:
#             return {
#                 "pointcut": POINTCUT_PATTERN.match(entry['pointcut']),
#                 "expected": entry['expected'][0],
#                 "observed": entry['unexpected'][0]
#             }
#     return None


def load_diffs(hint_folder):
    diff_file_path = join(hint_folder, "diff.json")
    if not exists(diff_file_path):
        return dict()

    diff_data = load_json(diff_file_path)
    return {
        entry['pointcut']:
        {
            "pointcut": POINTCUT_PATTERN.match(entry['pointcut']).groupdict(),
            "expected": entry['expected'][0],
            "observed": entry['unexpected'][0]
        }
        for entry in diff_data if len(entry['expected']) == 1}


def get_hints(hint_folder, test_cases, method_locations):
    mutation_data = load_json(join(hint_folder, 'mutation.json'))
    hint_data = load_json(join(hint_folder, 'hints.json'))
    diffs = load_diffs(hint_folder)
    diff_list = list(diffs.values())

    if type(hint_data) != list:  # More than one hint, pick the first one
        hint_data = [hint_data]

    def method_name(method_obj):
        return f"{method_obj['class']}.{method_obj['method']}{signature(method_obj['desc'])}"

    for item in hint_data:
        hint = {'type': item['hint-type']}
        if hint['type'] == 'infection':
            hint['targets'] = [method_name(entry)
                               for entry in item['entry-points']]
            hint['direct_access'] = mutated_method_is_accessible(
                mutation_data, hint['targets'])
        if hint['type'] == 'observation':
            hint['location'] = item['location']
        if 'accessors' in item:
            hint['targets'] = [method_name(entry)
                               for entry in item['accessors']]

        concrete_diff = None
        if 'pointcut' in item:
            concrete_diff = diffs.get(item['pointcut'], None)
        if diff_list:
            concrete_diff = diff_list[0]

        # If the hint is an observation we need the pointcut, otherwise there is no specific pointcut and could be any diff
        # TODO: Check this asymmetry with the other hints

        yield {
            "mutation": {
                'mutator': mutation_data['mutator'],
                'full_class_name': f'{mutation_data["package"]}.{mutation_data["class"]}',
                'method': mutation_data['method'],
                'description': mutation_data['description'],
                'signature': signature(mutation_data['description']),
                'is_void': is_void(mutation_data['description']),
                'tests': test_cases.get(mutation_id(**mutation_data), mutation_data['tests']),
                'line': method_locations.get(f'{mutation_data["package"]}.{mutation_data["class"]}.{mutation_data["method"]}{mutation_data["description"]}', None)
            },
            "hint": hint,
            "diff": concrete_diff

        }


def generate_readable_report(hint_folder, template, test_cases, method_locations):
    with open(join(hint_folder, 'report.md'), 'w') as report:
        for info in get_hints(hint_folder, test_cases, method_locations):
            report.write(template.render(info))


def mutation_id(**mutation):
    return f'{mutation["package"]}.{mutation["class"]}.{mutation["method"]}{mutation["description"]}{mutation["mutator"]}'


def load_test_cases(mutation_report):
    data = load_json(mutation_report)
    result = dict()
    for mutation in data['mutations']:
        if mutation['status'] != 'SURVIVED':
            continue
        tests = mutation['tests']['ordered']
        new_tests = []
        for case in tests:
            match = match_test_case(case)
            if not match:
                new_tests.append({'class': case})
            else:
                new_tests.append({'class': match.group(
                    "class"), 'method': match.group("method")})
        result[mutation_id(mutator=mutation['mutator'], method=mutation['method']
                           ['name'], **mutation['method'])] = new_tests
    return result


def signature(description):
    return DESCRIPTION_PARSER.parse(description)[0]


def is_void(description):
    return DESCRIPTION_PARSER.parse(description)[1] == 'void'


def create_template(project_info):

    env = Environment()

    def class_url(name):
        return f"https://github.com/{project_info['project']}/blob/{project_info['revision']}/{project_info['folder']}/src/main/java/{name.replace('.', '/')}.java"

    def class_link(name):
        inner_class_index = name.find('$')
        if inner_class_index > 0:
            class_name = name[0:inner_class_index]
        class_name = name.replace('.', '/')
        return f"[`{name}`]({class_url('name')})"

    def test_case_link(test_case):
        name = test_case['class']
        if 'method' in test_case and test_case['method']:
            name += '.' + test_case['method']
        return f"[`{name}`](https://github.com/{project_info['project']}/blob/{project_info['revision']}/{project_info['folder']}/src/test/java/{test_case['class'].replace('.', '/')}.java)"

    def method_link(mutation):
        method = f"{mutation['full_class_name']}.{mutation['method']}{signature(mutation['description'])}"
        line = f"#L{mutation['line']}" if mutation['line'] is not None else ""
        return f"[`{method}`]({class_url(mutation['full_class_name'])}{line})"

    def file_url(path):
        base_folder = join(project_info['project'].split(
            '/')[-1], project_info['folder'])
        index = path.find(base_folder)
        if index < 0:
            return ''
        return f"https://github.com/{project_info['project']}/blob/{project_info['revision']}/{project_info['folder']}{path[index+len(base_folder):]}"

    def location_link(location):
        path = location['file']
        url = file_url(path)
        if location['from']['line'] == location['to']['line']:
            url = f"{url}#L{location['from']['line']}"
        else:
            url = f"{url}#L{location['from']['line']}-L{location['to']['line']}"
        return f"[`{basename(path)}`]({url})"

    def describe(value):
        if "exceptionMessage" in value:
            return f'an exception of type `{value["typeName"]}` with message `{value["exceptionMessage"]}`'
        elif "literalValue" in value:
            return f'`{value["literalValue"]}`'
        elif 'isNull' in value:
            return "null" if value['isNull'] else "non-null"
        import pdb
        pdb.set_trace()
        raise ValueError("Should be one value or another")

    env.filters['ordinal'] = lambda value: 'f{value}{SUFFIXES[value%10]}'
    env.filters['class_link'] = class_link
    env.filters['test_case_link'] = test_case_link
    env.filters['describe'] = describe
    env.filters['method_link'] = method_link
    env.filters['location_link'] = location_link
    env.filters['signature'] = signature
    return env.from_string(REPORT_TEMPLATE)


def existing_folder(value):
    if not isdir(value):
        raise ValueError("Given value should be an existing folder")
    return value


def parse_args():
    parser = ArgumentParser()
    parser.add_argument("project_folder", type=existing_folder)
    return parser.parse_args()


def load_method_locations(xml_mutation_report):
    with open(xml_mutation_report) as _file:
        document = etree.parse(_file)
    return {
        f'{mutation.findtext("mutatedClass")}.{mutation.findtext("mutatedMethod")}{mutation.findtext("methodDescription")}':
        int(mutation.findtext('lineNumber'))
        for mutation in document.findall('mutation') if mutation.get('status') == 'SURVIVED'
    }


def generate_report_for_project(folder):
    project_info = load_json(join(folder, 'project.json'))
    print(project_info)
    template = create_template(project_info)
    test_cases = load_test_cases(join(folder, 'mutations.json'))
    xml_report_path = join(folder, 'mutations.xml')
    method_locations = load_method_locations(xml_report_path) if exists(
        xml_report_path) else {}  # Too bad I don't have the line number in the JSON report :(
    all_hints = glob(join(folder, 'observations', 'methods', '*', '*', 'hints.json')
                     ) + glob(join(folder, 'observations', 'tests', '*', 'hints.json'))
    print(join(folder, 'observations', 'methods', '*', '*', 'hints.json'))
    for hint_file in all_hints:
        print(hint_file)
        generate_readable_report(
            dirname(hint_file), template, test_cases, method_locations)


def main():
    arguments = parse_args()
    generate_report_for_project(arguments.project_folder)


if __name__ == '__main__':
    main()
