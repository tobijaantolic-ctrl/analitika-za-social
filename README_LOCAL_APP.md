# Analytics Dashboard kot lokalna aplikacija

Ta projekt ima macOS launcher, ki ob kliku:

1. pripravi samostojno runtime kopijo v `~/Library/Application Support/Analytics Dashboard/runtime`,
2. preveri Python virtualno okolje,
3. zažene lokalni strežnik na `http://127.0.0.1:5455`,
4. odpre dashboard v privzetem brskalniku,
5. prepreči podvojene strežniške procese.

## Namestitev

```bash
./install_macos_app.sh
```

Aplikacija se namesti v:

```text
~/Applications/Analytics Dashboard.app
```

Runtime kopija se namesti v:

```text
~/Library/Application Support/Analytics Dashboard/runtime
```

## Uporaba

Odpri `Analytics Dashboard.app` iz mape Applications.

Ročni ukazi:

```bash
"$HOME/Library/Application Support/Analytics Dashboard/runtime/scripts/desktop_control.sh" start
"$HOME/Library/Application Support/Analytics Dashboard/runtime/scripts/desktop_control.sh" open
"$HOME/Library/Application Support/Analytics Dashboard/runtime/scripts/desktop_control.sh" status
"$HOME/Library/Application Support/Analytics Dashboard/runtime/scripts/desktop_control.sh" stop
"$HOME/Library/Application Support/Analytics Dashboard/runtime/scripts/desktop_control.sh" logs
```

Podatki za aplikacijo so v runtime kopiji. Ob ponovni namestitvi installer ohrani obstoječi
`analytics.db`, da se ročno osveženi podatki ne povozijo.
