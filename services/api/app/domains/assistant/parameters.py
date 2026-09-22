"""Extract only explicit, schema-declared Skill inputs; never apply defaults."""

import json
import re

from jsonschema.validators import validator_for


def skill_parameters(schema, question, *, previous=None, missing=None):
    pending = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {'$ref', '$dynamicRef', '$recursiveRef'} and (not isinstance(value, str) or not value.startswith('#')):
                    raise ValueError('Skill Schema 不允许外部引用')
                pending.append(value)
        elif isinstance(node, list):
            pending.extend(node)
    validator_class = validator_for(schema)
    validator_class.check_schema(schema)
    validator = validator_class(schema)
    properties = schema.get('properties', {})
    arguments = {k: v for k, v in (previous or {}).items() if k in properties}
    extracted = {}
    structured = False
    try:
        value = json.loads(question)
        if isinstance(value, dict):
            structured = True
            extracted.update({k: v for k, v in value.items() if k in properties})
    except (ValueError, TypeError):
        pass
    aliases = {'material_code': ('物料编码', '物料编号', '物料'), 'organization': ('组织', '公司')}
    labels = {}
    for key, definition in properties.items():
        if key == 'question':
            extracted[key] = question
            continue
        names = [key, *aliases.get(key, ())]
        if isinstance(definition.get('title'), str):
            names.append(definition['title'])
        labels.update({name.lower(): key for name in names})
    invalid = set()
    if not structured and labels:
        # Values end at a field boundary, not at a whitelist of value characters.
        pattern = r'(?<![\w])(' + '|'.join(re.escape(n) for n in sorted(labels, key=len, reverse=True)) + r')\s*[=:：]\s*'
        matches = list(re.finditer(pattern, question, re.IGNORECASE))
        for index, match in enumerate(matches):
            key = labels[match.group(1).lower()]
            end = matches[index + 1].start() if index + 1 < len(matches) else len(question)
            value = question[match.end():end].strip().rstrip(',，;；').strip().strip('"\'')
            if key in extracted:
                invalid.add(key)
            extracted[key] = value
        if 'material_code' in properties and 'material_code' not in extracted:
            match = re.search(r'物料(?:编码|编号)?\s*([^，,；;\n]+?)(?:库存|$)', question)
            if match:
                extracted['material_code'] = match.group(1).strip()
    if not (set(extracted) - {'question'}) and len(missing or []) == 1 and missing[0] in properties:
        # The preceding persisted question identifies the one field being answered.
        extracted[missing[0]] = question.strip()
    for key, value in extracted.items():
        # An explicit correction supersedes the old value even when invalid.
        arguments.pop(key, None)
        if key in invalid:
            continue
        typ = properties[key].get('type')
        try:
            if typ == 'integer' and isinstance(value, str) and re.fullmatch(r'-?\d+', value):
                value = int(value)
            elif typ == 'number' and isinstance(value, str) and re.fullmatch(r'-?\d+(?:\.\d+)?', value):
                value = float(value)
            elif typ == 'boolean' and isinstance(value, str) and value.lower() in {'true', 'false'}:
                value = value.lower() == 'true'
        except ValueError:
            continue
        if validator.evolve(schema=properties[key]).is_valid(value):
            arguments[key] = value
        else:
            invalid.add(key)
    required = list(schema.get('required', []))
    missing_fields = [key for key in properties if key in invalid or (key in required and key not in arguments)]
    if not missing_fields and not validator.is_valid(arguments):
        # Cross-field/schema constraints must pass before any bound capability runs.
        missing_fields = list(properties) or ['有效输入']
    return arguments, missing_fields
