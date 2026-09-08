"""Deterministic paragraph comparisons; impact labels describe evidence, not legal conclusions."""
from difflib import SequenceMatcher


def compare_versions(before, after, *, report=None):
    left, right = before['segments'], after['segments']
    changes, matches, transitions = [], {}, {}

    def reference(segment, version):
        return {'version': version['version'], 'segmentId': segment['id'],
                'location': segment['location'], 'text': segment['text']}

    for kind, a, b, c, d in SequenceMatcher(None, [s['text'] for s in left], [s['text'] for s in right], autojunk=False).get_opcodes():
        if kind == 'equal':
            matches.update({i: j for i, j in zip(range(a, b), range(c, d))})
            continue
        for offset in range(max(b - a, d - c)):
            i, j = a + offset, c + offset
            old = reference(left[i], before) if i < b else None
            new = reference(right[j], after) if j < d else None
            change_type = 'modified' if old and new else 'deleted' if old else 'added'
            change = {'id': f"v{before['version']}:{old['segmentId'] if old else '-'}:v{after['version']}:{new['segmentId'] if new else '-'}",
                      'type': change_type, 'before': old, 'after': new}
            changes.append(change)
            if old:
                transitions[i] = change

    impacts = []
    clauses = {item.get('clauseId'): item.get('quote', '') for item in (report or {}).get('clauses', [])}
    for risk in (report or {}).get('risks', []):
        # Model clause IDs commonly differ from importer paragraph IDs. Prefer
        # verifiable quotes; IDs alone must not override a contradictory quote.
        quote = str(risk.get('quote') or clauses.get(risk.get('clauseId')) or '').strip()
        candidates = [i for i, segment in enumerate(left) if quote and quote in segment['text']]
        if not quote:
            candidates = [i for i, segment in enumerate(left) if segment['id'] == risk.get('clauseId')]
        status, basis, refs = 'review_required', '未能唯一定位原风险证据，请人工复核。', []
        if len(candidates) == 1:
            index = candidates[0]
            refs = [reference(left[index], before)]
            if index in matches:
                status, basis = 'still_valid', '原证据段落文字未变，仅表示证据仍存在；上下文和风险结论需人工核对。'
                refs.append(reference(right[matches[index]], after))
            else:
                change = transitions[index]
                if change['type'] == 'deleted' and not any((quote or left[index]['text']) in segment['text'] for segment in right):
                    status, basis = 'removed', '原证据段落已删除且未在目标版本找到相同证据；不代表法律风险已消除。'
                else:
                    basis = '原证据段落已修改或移动，风险结论需要重新审查。'
                if change['after']:
                    refs.append(change['after'])
        impacts.append({'riskId': risk.get('id'), 'clauseId': risk.get('clauseId'), 'quote': quote,
                        'status': status, 'basis': basis, 'references': refs})

    return {'assetId': before['assetId'], 'fromVersion': before['version'], 'toVersion': after['version'],
            'changes': changes, 'riskImpacts': impacts,
            'notice': '段落差异和风险影响基于文字定位，不提供语义确定性或法律结论。'}
