path = "/Users/ekaterinakulesova/git-repos/knowledge_graph/harry_with_years.owl"
with open(path, "rb") as f:
    head = f.read(1024)
print(head[:200])  # байтовый дамп
print("---")
try:
    txt = head.decode("utf-8")
except UnicodeDecodeError:
    txt = head.decode("latin-1")
print(repr(txt))
