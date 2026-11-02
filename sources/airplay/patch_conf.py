P = "/usr/local/etc/shairport-sync.conf"

try:
    src = open(P).read()
except FileNotFoundError:
    raise SystemExit(f"{P} non trovato: shairport-sync installato?")

if 'output_backend = "stdout"' in src:
    print("config già impostata")
    raise SystemExit(0)

needle = "general =\n{\n"
if needle not in src:
    raise SystemExit("blocco general non trovato")

src = src.replace(
    needle,
    needle + '    name = "Cucina AirPlay";\n'
              '    output_backend = "stdout";\n',
    1)
open(P, "w").write(src)
print("shairport-sync configurato: name=Cucina AirPlay, backend=stdout")
