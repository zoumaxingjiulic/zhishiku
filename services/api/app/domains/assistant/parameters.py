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
    try:
        value = json.loads(question)
        if isinstance(value, dict):
            extracted.update({k: v for k, v in value.items() if k in properties})
    except (ValueError, TypeError):
        pass
    aliases = {'material_code': ('物料编码', '物料编号', '物料'), 'organization': ('组织', '公司')}
    for key, definition in properties.items():
        if key == 'question':
            extracted[key] = question
            continue
        names = [key, *aliases.get(key, ())]
        if isinstance(definition.get('title'), str):
            names.append(definition['title'])
        for name in names:
            # Unlabelled natural-language text is not a source of guessed fields.
            pattern = (rf'{re.escape(name)}\s*[=:：]?\s*([A-Za-z0-9_.-]+)' if key == 'material_code'
                       else rf'{re.escape(name)}\s*[=:：]\s*([^，,；;\n]+)')
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                extracted[key] = match.group(1).strip().strip('"\'')
                break
    if not (set(extracted) - {'question'}) and len(missing or []) == 1 and missing[0] in properties:
        # The preceding persisted question identifies the one field being answered.
        extracted[missing[0]] = question.strip()
    for key, value in extracted.items():
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
    required = list(schema.get('required', []))
    missing_fields = [key for key in required if key not in arguments]
    if not missing_fields and not validator.is_valid(arguments):
        # Cross-field/schema constraints must pass before any bound capability runs.
        missing_fields = list(properties) or ['有效输入']
    return arguments, missing_fields
