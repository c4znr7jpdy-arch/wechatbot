import io, json, re
f = io.open("Py/wechat_service.log", "r", encoding="utf-8", errors="replace")
lines = f.readlines()
# Find a full 11047 line and print all top-level keys
for l in lines:
    if "[SEND RESP 11047] data=" in l and "is_pc" in l:
        idx = l.find("data=")
        raw = l[idx+5:].rstrip()
        try:
            obj = json.loads(raw)
        except Exception:
            # log truncates at 500 chars; try to find the keys present
            keys = re.findall(r'"([\w]+)":', raw)
            print("11047 data keys (truncated log):", sorted(set(keys)))
            print("sample raw (first 700):", raw[:700])
            break
        print("11047 top-level keys:", list(obj.keys()))
        for k, v in obj.items():
            sv = str(v)
            print(f"  {k} = {sv[:90]}")
        break
print()
# Also check: does 11047 ever carry to_wxid or room_wxid?
has_to = sum(1 for l in lines if "[SEND RESP 11047]" in l and '"to_wxid"' in l)
has_room = sum(1 for l in lines if "[SEND RESP 11047]" in l and '"room_wxid"' in l)
print("11047 lines with to_wxid field:", has_to)
print("11047 lines with room_wxid field:", has_room)
