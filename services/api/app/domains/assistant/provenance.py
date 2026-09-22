"""Bounded authorization lineage, containing identifiers and versions only."""

from ...core.errors import AuthorizationError


FIELDS = ('knowledge_base_ids', 'tool_ids', 'agent_ids', 'skill_ids')


def ids(values):
    if not isinstance(values, (list, tuple)) or len(values) > 256:
        raise AuthorizationError('来源依赖超过限制')
    if any(type(v) is not int or v <= 0 for v in values):
        raise AuthorizationError('来源依赖无效')
    return sorted(set(values))


def normalize(source):
    if not isinstance(source, dict) or source.get('version') != 1:
        raise AuthorizationError('来源依赖无效')
    out = {'version': 1, 'document_ids': ids(source.get('document_ids', [])),
           'selection': {f: ids(source.get('selection', {}).get(f, [])) for f in FIELDS},
           'agents': {}, 'skills': {}}
    for kind, version_key in (('agents', 'config_version'), ('skills', 'version')):
        entries = source.get(kind, {})
        if not isinstance(entries, dict) or len(entries) > 256:
            raise AuthorizationError('来源依赖超过限制')
        for key, entry in entries.items():
            resource_id = int(key)
            if resource_id <= 0 or not isinstance(entry, dict):
                raise AuthorizationError('来源依赖无效')
            version = entry.get(version_key)
            if version is not None and (type(version) is not int or version < 0):
                raise AuthorizationError('来源版本无效')
            data = {version_key: version}
            if kind == 'agents':
                data.update({f: ids(entry.get(f, [])) for f in ('knowledge_base_ids', 'tool_ids')})
            else:
                data['selection'] = {f: ids(entry.get('selection', {}).get(f, [])) for f in FIELDS[:3]}
            out[kind][str(resource_id)] = data
            field = 'agent_ids' if kind == 'agents' else 'skill_ids'
            out['selection'][field] = ids([*out['selection'][field], resource_id])
            dependencies = {f: data[f] for f in ('knowledge_base_ids', 'tool_ids')} if kind == 'agents' else data['selection']
            for field, values in dependencies.items():
                out['selection'][field] = ids(sorted(set(out['selection'][field]) | set(values)))
    dependency_count = sum(len(e[f]) for e in out['agents'].values() for f in ('knowledge_base_ids', 'tool_ids'))
    dependency_count += sum(len(v) for e in out['skills'].values() for v in e['selection'].values())
    if sum(len(v) for v in out['selection'].values()) + len(out['document_ids']) + dependency_count > 512:
        raise AuthorizationError('来源依赖超过限制')
    return out


def merge(*sources):
    out = normalize({'version': 1})
    for raw in sources:
        source = normalize(raw)
        out['document_ids'] = ids(out['document_ids'] + source['document_ids'])
        for field in FIELDS:
            out['selection'][field] = ids(out['selection'][field] + source['selection'][field])
        for kind in ('agents', 'skills'):
            for key, value in source[kind].items():
                if key in out[kind] and out[kind][key] != value:
                    raise AuthorizationError('历史来源配置已变更')
                out[kind][key] = value
    return normalize(out)


def from_execution(root_id, selection, evidence, result):
    selected = selection.model_dump()
    for field in FIELDS:
        selected[field] = sorted(set(selected[field]) | set(evidence.get('selection', {}).get(field, [])))
    selected['tool_ids'] = sorted(set(selected['tool_ids']) | {
        e['connector_tool_id'] for e in result.get('tool_calls', []) if e.get('connector_tool_id')})
    agents = {str(k): v for k, v in evidence.get('agents', {}).items() if int(k) != root_id}
    skills = {str(k): v for k, v in evidence.get('skills', {}).items()}
    selected['agent_ids'] = sorted(set(selected['agent_ids']) | {int(k) for k in agents})
    selected['skill_ids'] = sorted(set(selected['skill_ids']) | {int(k) for k in skills})
    return merge({'version': 1, 'selection': selected, 'agents': agents, 'skills': skills,
                  'document_ids': [c['document_id'] for c in result.get('citations', [])]},
                 evidence.get('history_authority', {'version': 1}))


def validate_dependencies(source, repository, assistant_repository, *, locked=False):
    """ACL membership is checked by the catalog; here check dependency versions."""
    for key, expected in source['agents'].items():
        aid = int(key)
        actual = (assistant_repository.locked_agents.get(aid) if locked else repository.get_agent(aid))
        if not actual or actual.get('status') != 'active' or actual.get('config_version') != expected['config_version']:
            raise AuthorizationError('历史智能体配置已变更')
        kbs = repository.agent_knowledge_base_ids(aid)
        if set(kbs) != set(expected['knowledge_base_ids']):
            raise AuthorizationError('历史智能体知识绑定已变更')
        bindings = (assistant_repository.locked_bindings if locked else repository.bound_tools(aid))
        bound_ids = {b.get('connector_tool_id', b.get('id')) for b in bindings
                     if b.get('agent_id', aid) == aid and b.get('permission', 'read') == 'read'}
        if not set(expected['tool_ids']).issubset(bound_ids):
            raise AuthorizationError('历史智能体工具绑定已变更')
    for key, expected in source['skills'].items():
        actual = assistant_repository.skill(int(key))
        if not actual or actual.get('version') != expected['version']:
            raise AuthorizationError('历史 Skill 配置已变更')
        if any(set(actual.get(f, [])) != set(v) for f, v in expected['selection'].items()):
            raise AuthorizationError('历史 Skill 绑定已变更')
