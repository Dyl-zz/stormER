"""Row -> JSON dicts shared across routes. Plain functions, no magic."""

from app.spine.models import Branch, Document, Node, NodeExecution, ReferenceChip


def document_json(document: Document) -> dict:
    return {
        "id": str(document.id),
        "owner_id": str(document.owner_id),
        "title": document.title,
        "created_at": document.created_at.isoformat(),
    }


def branch_json(branch: Branch) -> dict:
    return {
        "id": str(branch.id),
        "document_id": str(branch.document_id),
        "name": branch.name,
        "color": branch.color,
        "ordinal": branch.ordinal,
    }


def node_json(node: Node) -> dict:
    return {
        "id": str(node.id),
        "branch_id": str(node.branch_id),
        "type": node.type,
        "title": node.title,
        "ordinal": node.ordinal,
        "parameters": node.parameters,
        "declared_inputs": node.declared_inputs,
    }


def execution_json(execution: NodeExecution) -> dict:
    return {
        "id": str(execution.id),
        "node_id": str(execution.node_id),
        "status": execution.status.value,
        "label": execution.label.value,
        "created_at": execution.created_at.isoformat(),
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
        "generated_text": execution.generated_text,
        "input_keys": execution.input_keys,
        "error": execution.error,
        "raw_data_uri": execution.raw_data_uri,
        "raw_data_state": execution.raw_data_state.value if execution.raw_data_state else None,
        "raw_data_meta": execution.raw_data_meta,
    }


def chip_json(chip: ReferenceChip) -> dict:
    return {
        "id": str(chip.id),
        "center_node_id": str(chip.center_node_id),
        "node_id": str(chip.node_id),
        "execution_id": str(chip.execution_id),
        "marker": chip.marker,
        "token": f"{{{{chip:{chip.marker}}}}}",
    }
