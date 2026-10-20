# Viri in nadomestne poti

Preverjeno 2026-10-20 iz oblačnega okolja (omrežna politika).

## Stanje
- Neposreden dostop do spletnih strani (curl, WebFetch): **blokiran (403 CONNECT)** za vse preizkušene domene:
  rtvslo.si, 24ur.com, siol.net, druzina.si, katoliska-cerkev.si, ognjisce.si, domovina.je, nova24tv.si,
  mladina.si, youtube.com, tiktok.com, reddit.com, trends.google.com, google.com, duckduckgo.com,
  wikipedia.org, vaticannews.va, cath.ch, pewresearch.org, podcasts.apple.com, open.spotify.com.
- **WebSearch deluje** (vrne naslove, URL-je in povzetke, ne celotnih strani).

## Nadomestne poti (po vrsti)
1. WebSearch, `standard`; `extended` samo če je rezultat tanek.
2. Iskanje po domeni: `site:rtvslo.si`, `site:24ur.com`, `site:siol.net`, `site:druzina.si`,
   `site:katoliska-cerkev.si`, `site:ognjisce.si`, `site:domovina.je`, `site:delo.si` + tema + mesec.
3. Za gostje: iskanje "<ime> intervju podkast" in "<ime> gost" – potrdi z vsaj dvema neodvisnima zadetkoma.
4. Če WebSearch ne vrne ničesar iz zadnjih 14 dni, to v poročilu izrecno napiši; ničesar si ne izmišljaj.
5. Trajna rešitev: v nastavitvah okolja (Network access) dodaj domene zgoraj pod Allowed domains.

## Pravila
- Vsaka trditev v poročilu ima URL iz WebSearch rezultata.
- Pri vsakem krogu na začetku na hitro znova preveri dostop (1 poizvedba) in posodobi to datoteko, če se kaj spremeni.
