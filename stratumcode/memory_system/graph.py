from __future__ import annotations

from . import store
from .normalization import canonical_subject_key


def graph_data(workspace_dir: str) -> dict:
    data = store.graph(workspace_dir)
    nodes = []
    edges = []
    record_ids = {record.get("id") for record in data["records"]}
    supported_record_ids = {item.get("record_id") for item in data["evidence"]}
    subject_ids = set()
    for record in data["records"]:
        subject = _subject_id(workspace_dir, record)
        if subject not in subject_ids:
            subject_ids.add(subject)
            nodes.append({
                "id": subject,
                "type": "subject",
                "label": _subject_label(workspace_dir, record),
                "kind": record.get("subject_kind") or "subject",
            })
        nodes.append({
            "id": record["id"],
            "type": "memory",
            "label": record.get("statement", "")[:90],
            "kind": record.get("kind", ""),
            "status": record.get("status", ""),
            "freshness": record.get("freshness", ""),
            "record": record,
        })
        edges.append({"id": f"{subject}->{record['id']}", "source": subject, "target": record["id"], "label": "has"})
        for source_id in record.get("source_record_ids") or []:
            if source_id in record_ids:
                edges.append({
                    "id": f"{source_id}->{record['id']}:derived_from",
                    "source": source_id,
                    "target": record["id"],
                    "label": "derived_from",
                })
    for evidence in data["evidence"]:
        evidence_id = evidence["id"]
        nodes.append({
            "id": evidence_id,
            "type": "evidence",
            "label": evidence.get("excerpt", "")[:90] or evidence.get("path", "") or "evidence",
            "kind": evidence.get("kind", ""),
            "evidence": evidence,
        })
        edges.append({"id": f"{evidence_id}->{evidence['record_id']}", "source": evidence_id, "target": evidence["record_id"], "label": "supports"})
    for link in data["links"]:
        edges.append({
            "id": f"{link['source_id']}->{link['target_id']}:{link['relation']}",
            "source": link["source_id"],
            "target": link["target_id"],
            "label": link["relation"],
        })
    return {
        "nodes": nodes,
        "edges": edges,
        "records": data["records"],
        "diagnostics": _diagnostics(workspace_dir, data["records"], supported_record_ids, data["links"]),
    }


def _subject_id(workspace_dir: str, record: dict) -> str:
    kind = str(record.get("subject_kind") or "subject")
    if kind == "project":
        return "subject:project"
    return f"subject:{kind}:{canonical_subject_key(workspace_dir, kind, str(record.get('subject_key') or ''))}"


def _subject_label(workspace_dir: str, record: dict) -> str:
    kind = str(record.get("subject_kind") or "subject")
    if kind == "project":
        return "Project knowledge"
    return canonical_subject_key(workspace_dir, kind, str(record.get("subject_key") or "")) or kind or "subject"


def _diagnostics(workspace_dir: str, records: list[dict], supported_record_ids: set[str], links: list[dict]) -> dict:
    subject_keys = {}
    for record in records:
        kind = str(record.get("subject_kind") or "subject")
        canonical = canonical_subject_key(workspace_dir, kind, str(record.get("subject_key") or ""))
        subject_keys.setdefault((kind, canonical), set()).add(str(record.get("subject_key") or ""))
    duplicate_subjects = [
        {"subject_kind": kind, "canonical_key": key, "raw_keys": sorted(values)}
        for (kind, key), values in subject_keys.items()
        if len(values) > 1
    ]
    unsupported_verified = [
        str(record.get("id") or "")
        for record in records
        if _unsupported_verified(record, supported_record_ids)
    ]
    return {
        "duplicate_subjects": duplicate_subjects,
        "unsupported_verified_record_ids": unsupported_verified,
        "conflict_edges": sum(1 for link in links if link.get("relation") == "conflicts"),
        "derived_edges": sum(1 for link in links if link.get("relation") in {"derived_from", "summarizes"}),
    }


def _unsupported_verified(record: dict, supported_record_ids: set[str]) -> bool:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
    if audit.get("downgraded_reason") == "verified_without_evidence":
        return True
    return record.get("confidence") == "verified" and record.get("id") not in supported_record_ids
