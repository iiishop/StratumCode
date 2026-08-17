from __future__ import annotations

from . import store


def graph_data(workspace_dir: str) -> dict:
    data = store.graph(workspace_dir)
    nodes = []
    edges = []
    subject_ids = set()
    for record in data["records"]:
        subject = _subject_id(record)
        if subject not in subject_ids:
            subject_ids.add(subject)
            nodes.append({
                "id": subject,
                "type": "subject",
                "label": _subject_label(record),
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
    return {"nodes": nodes, "edges": edges, "records": data["records"]}


def _subject_id(record: dict) -> str:
    kind = str(record.get("subject_kind") or "subject")
    if kind == "project":
        return "subject:project"
    return f"subject:{kind}:{record.get('subject_key')}"


def _subject_label(record: dict) -> str:
    kind = str(record.get("subject_kind") or "subject")
    if kind == "project":
        return "Project knowledge"
    return str(record.get("subject_key") or kind or "subject")
