"""Native Chat Completions function definitions for the existing local tools."""
from __future__ import annotations


def object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def definitions(role: str, final_fields: dict, parallel: bool) -> list[dict]:
    text = {"type": "string"}
    specs = [
        ("list_files", "List files in the actual workspace.", object_schema({})),
        ("read_file", "Read a workspace file before deciding or editing. Lines are numbered.",
         object_schema({"path": text, "start": {"type": "integer", "minimum": 1},
                        "count": {"type": "integer", "minimum": 1, "maximum": 500}}, ["path"])),
        ("search", "Search workspace files for a literal string.", object_schema({"query": text})),
    ]
    if role == "coder":
        specs += [
            ("write_file", "Create or overwrite a file in your workspace. This actually writes the code.",
             object_schema({"path": text, "content": text})),
            ("replace_text", "Replace one unique exact substring in an existing file.",
             object_schema({"path": text, "old": text, "new": text})),
            ("delete_file", "Delete an assigned file from your workspace.", object_schema({"path": text})),
            ("run", "Execute a command in your workspace, subject to user approval.",
             object_schema({"argv": {"type": "array", "items": text, "minItems": 1}})),
        ]
    properties = {key: {"type": "boolean" if kind is bool else "string"}
                  for key, kind in final_fields.items()}
    if role == "planner" and parallel:
        properties["tasks"] = {"type": "array", "minItems": 1, "items": object_schema({
            "title": text, "approach": text, "acceptance": text,
            "files": {"type": "array", "items": text, "minItems": 1},
        })}
    specs.append(("finish", "Finish this role with its structured result. Coders must use file tools to inspect and implement before finishing.", object_schema(properties)))
    return [{"type": "function", "function": {"name": name, "description": description,
                                               "parameters": parameters, "strict": name != "read_file"}}
            for name, description, parameters in specs]
