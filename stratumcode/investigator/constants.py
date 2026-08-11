from __future__ import annotations

import re

INVESTIGATION_CAPABILITY = "investigation"
PROJECT_EVIDENCE_CAPABILITY = "investigation.project_evidence"
MAX_REPEATED_TOOL_ERRORS = 3
READ_ONLY_SUMMARY_MIN_RESOLUTION_RATIO = 0.35
MAX_REPEATED_RECORD_NO_PROGRESS = 3
MAX_DUPLICATE_NO_PROGRESS = 2
MAX_PENDING_DISCOVERY_OBSERVATIONS = 8
REQUIRED_FINDING_SLOT_ATTEMPTS = 2
REQUIRED_AUDIT_ATTEMPTS = 2
# During semantic repair the model may still gather missing evidence, but
# must not finish or resolve until the audit passes.
_REPAIR_ALLOWED_TOOL_NAMES = frozenset({
    "read",
    "grep",
    "glob",
    "code_nav",
    "lsp_tool",
    "record_investigation_findings",
})
OBSERVATION_EVIDENCE_CHARS = 8000
GROUNDING_LITERAL_SPAN_CONTEXT_LINES = 2
GROUNDING_LITERAL_SPAN_MAX_LINES = 16
GROUNDING_LITERAL_SPAN_MAX_LINE_CHARS = 360
GROUNDING_LITERAL_SPAN_MAX_ITEMS = 12
PROJECT_FILE_SCAN_LIMIT = 20000
DISCOVERY_CONTRACT_FIELDS = (
    "hypothesis",
    "expected_observation",
    "decision_impact",
    "stop_condition",
)
FINDING_FIELDS = (
    "beliefs",
    "resolutions",
    "new_unknowns",
    "unknowns",
    "user_decisions_required",
    "task_updates",
)
RESOLUTION_KINDS = {"direct_fact", "derived_inference", "user_decision", "deferred"}
SEMANTIC_AUDIT_KINDS = {"derived_inference"}
CLEARIFY_RESOLUTION_REASON = "Answered by the user through clearify."
CLEARIFY_UNRESOLVED_REASON = "User could not answer through clearify; continue project investigation."
GROUNDING_LITERAL_REASON_PREFIX = "Cited observations do not contain the claimed code literal(s):"
STATE_WRITE_REASON_PREFIX = "Cited observations contain state writes omitted from the resolution:"

# 否定性结论（absence）特征词：答案声称"不存在/未找到/未定义"时，
# grounding 检查降级（见 _resolution_is_absence_claim）。
_NEGATIVE_CLAIM_RE = re.compile(
    r"(未找到|未发现|未定义|未描述|未提供|未提及|未记录|没有找到|不存在|"
    r"没有独立|无独立|没有任何|均未|"
    r"not found|does not exist|absent|no evidence|undocumented)"
)

# read_only 模式下按设计不可得的"运行时证据"要求（审计模型常误提）：
# 这类 missing 在只读调查中被过滤，不进入 REPAIR（见 _apply_investigation_audit）。
_RUNTIME_EVIDENCE_RE = re.compile(
    r"(运行时|runtime|测试|测试用例|日志|复现|reproduce|"
    r"实际运行|运行表现|可观察.*运行|运行.*验证|跑一下|执行.*验证|"
    r"可复现行为)"
)
RECORD_RECOVERY_REASON = "Record pending observations and required resolutions."

# 其他语言的框架/语言级根命名空间：与 Python 标准库同理，它们是语言环境
# 的一部分，项目里没有对应源文件，grep/read 永远产生不了这些引用
# （System.Math.Sqrt / UnityEngine.Debug.Log / java.lang.Math.sqrt /
# console.log / std::vector / fmt.Println 等）。模型在答案里引用它们
# 是"计划使用框架 API"，不是"声称项目里有这段代码"，无需观察证据。
_FRAMEWORK_ROOTS = frozenset({
    # .NET / C# / F# / VB
    "System", "Microsoft", "Windows", "System.Runtime",
    # Unity（C# 变种：引擎级命名空间）
    "UnityEngine", "UnityEditor",
    # Java / Kotlin / JVM
    "java", "javax", "jakarta", "jdk", "kotlin",
    # Rust
    "std", "core", "alloc",
    # C / C++（std:: 已被提取规则跳过，这里兜底 boost 等）
    "boost", "glib",
    # Go 标准库包
    "bufio", "bytes", "container", "crypto", "database", "encoding", "errors",
    "flag", "fmt", "hash", "html", "image", "index", "io", "log", "math", "mime",
    "net", "os", "path", "reflect", "regexp", "runtime", "sort", "strconv",
    "strings", "sync", "syscall", "testing", "text", "time", "unicode",
    "unsafe",
    # JavaScript / TypeScript 内置全局
    "Array", "BigInt", "Date", "Intl", "JSON", "Map", "Math", "Number",
    "Object", "Promise", "Proxy", "Reflect", "RegExp", "Set", "String",
    "Symbol", "WeakMap", "WeakSet", "console", "fetch", "globalThis",
    "navigator", "window", "document", "WebSocket",
    # PHP
    "PHP", "Spl",
})

_FILE_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9][A-Za-z0-9_+-]{0,15})"
    r"(?![A-Za-z0-9_$])"
)

_FILE_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9][A-Za-z0-9_+-]{0,15})"
    r"(?:\s|[^A-Za-z0-9_$./\\-]){0,8}([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\))?"
)

# Generic language/stdlib/argument words that must never be treated as
# project symbols by the LSP definition check. Real project function names
# (create, rename, generate_title, ...) must NOT be listed here.
_DEF_READ_NOISE_SYMBOLS = frozenset({
    "def", "if", "elif", "else", "for", "while", "return", "not", "and", "or",
    "in", "is", "with", "as", "try", "except", "finally", "raise", "yield",
    "lambda", "pass", "break", "continue", "import", "from", "class", "assert",
    "del", "global", "nonlocal",
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set",
    "tuple", "type", "range", "sum", "min", "max", "sorted", "enumerate",
    "zip", "map", "filter", "any", "all", "isinstance", "issubclass", "getattr",
    "setattr", "hasattr", "repr", "format", "open", "id", "hash", "iter", "next",
    "object", "property", "staticmethod", "classmethod", "super", "vars", "dir",
    "abs", "round", "divmod", "pow", "ord", "chr", "hex", "oct", "bin", "bytes",
    "bytearray", "memoryview", "slice", "frozenset", "complex", "input", "eval",
    "exec", "compile", "globals", "locals", "callable", "ascii", "help", "exit",
    "json", "re", "os", "sys", "time", "datetime", "pathlib", "Path", "shutil",
    "subprocess", "uuid", "collections", "defaultdict", "Counter", "deque",
    "functools", "itertools", "typing", "Optional", "Any", "List", "Dict", "Set",
    "Tuple", "Union", "Callable", "Iterator", "Generator", "Iterable", "Mapping",
    "math", "cmath", "numpy", "np",
    "startswith", "endswith", "strip", "split", "join", "replace", "lower",
    "upper", "capitalize", "title", "find", "index", "count", "append", "extend",
    "insert", "remove", "pop", "clear", "sort", "reverse", "copy", "setdefault",
    "update", "keys", "values", "items", "add", "discard", "union", "intersection",
    "difference", "issubset", "issuperset", "encode", "decode", "zfill", "ljust",
    "rjust", "partition", "rpartition", "splitlines", "expandtabs", "maketrans",
    "translate", "self", "data", "item", "value", "key", "text", "content",
    "message", "event", "run", "id", "name", "title", "state", "status", "reason",
    "answer", "summary", "output", "input", "result", "error", "exc", "request",
    "response", "path", "file", "line", "index", "kind", "source", "target",
    "session", "workspace", "model", "provider", "analysis", "investigation",
    "resolution", "belief", "observation", "evidence", "unknown", "task", "goal",
    "acceptance", "requirement", "tool", "call", "function", "fn", "args",
    "kwargs", "prev", "current", "total", "count", "size", "length", "before",
    "after", "start", "end", "done", "fail", "success", "ok", "true", "false",
    "none", "null", "python", "node", "js", "ts", "vue", "css", "html", "md",
    "txt", "log", "default", "generate", "select", "seed", "final", "finish",
    "handle", "process", "apply", "resolve", "check", "validate", "parse",
    "convert", "merge", "normalize", "search", "find", "read", "write", "grep",
    "get", "set", "known", "items", "values", "keys", "status", "action",
    "kind", "field", "fields", "record", "records", "store", "stores",
    "explain", "describe", "show", "mention", "note", "include", "cover",
})

LSP_DEFINITION_NOT_FOUND = "___lsp_definition_not_found___"
LSP_DEFINITION_UNAVAILABLE = "___lsp_definition_unavailable___"

_EXTENSION_LANGUAGE = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescriptreact", ".jsx": "javascriptreact",
    ".vue": "vue", ".svelte": "svelte",
    ".cs": "csharp", ".fs": "fsharp", ".vb": "vb",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hxx": "cpp",
    ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".lua": "lua", ".r": "r", ".m": "objective-c", ".mm": "objective-cpp",
    ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
    ".hs": "haskell", ".erl": "erlang", ".ml": "ocaml",
    ".sql": "sql", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".html": "html", ".css": "css", ".scss": "scss",
}
