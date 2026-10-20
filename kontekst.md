# Kontekst projekta AVTOMATIZERANJE (stanje 16. 10. 2026)

## Kdo in kaj
- Tobija Antolič, glavni producent platforme Kdo kakor On (KKO): YouTube, podkasti, socialna omrežja. Slovenščina.
- Podkasta: **Martin Golob** in **Med ovcami** (Urban Šifrar, Ambrož Kastelic; ep. 22 in 24 vodil Tobija).
- Obseg: 3–4 epizode, 15–30 reelov/mesec. Cilj: odpreš Premiere in tam je ~10 zrezanih reelov.
- O vsebini KKO odloča Tobija sam.

## Pravila sodelovanja
- Najprej preberi, vprašaj, pokaži načrt, nato začni.
- Nikoli ne briši, prepisuj, izvažaj ali shranjuj (tudi Premiere projekta) brez izrecnega dovoljenja. Klipe raje disable kot briši.
- Glavne sekvence se ne dotikaj, razen za izrecno zahtevano (npr. podnapisi).
- Ob napaki orodja se ustavi in povej.
- Kratko, direktno, brez laskanja; iskreno ugovarjaj.
- Količinske kvote (»točno 10 klipov«) so zavrnjene; velja prag kakovosti.
- Na koncu dela: tabela narejenega + opomnik, da projekt ni shranjen.

## Mape (Mac)
- Epizode: `~/Documents/Podkasti/<ime epizode>/` (pine pod, ales pod, test-jernej, animacije, matjaz pod).
- Starejše: `/Volumes/2T/KDO KAKOR ON/...` (drugačna shema JSON: `segments/start/end/text`).
- Pravila in korpus: `~/kko/dnevnik.db` (tabela `pravilo`), `~/kko/destilat/pravila.md`.
- Skripte: `~/.hermes/scripts/` (`nocni.py`, `nocni_asr.py`, `nocni_opis.py`), delo v `~/.hermes/work/<epizoda>/`.

## Prepisovanje (nočni cevovod Hermes)
- Cron 23:00: Whisper → popravki Ollama → `opis.md` → `energija.json`. Izhod: `transkript.json`, `.srt`, `.txt`, `popravki.md`, `energija.json`, `opis.md`; `.dela` med obdelavo.
- Shema: `segmenti[].z/k/besedilo`, `besede[].b/z/k`.
- Stanje: `nocni_asr.py` = faster-whisper large-v3 CPU int8 (~realni čas). MLX je v `nocni_asr_mlx.py.prej` (Tobija: »prestavi nazaj«).
- Ollama `qwen3.8:27b-mlx` (~53 GB) je prevelik za 36 GB RAM (swap, timeouti). Predlog: GaMS3-12B ali brez LLM popravkov. **Ni odločeno.**
- MLX ne vzporedno z Ollamo.
- Iz oblaka Whisper ne dela (huggingface blokiran): prepis vedno na Macu.
- Dolge procese z `nohup … &` prek Desktop Commanderja, preverjaj s `send_later`.

## Skilli
`opisi`, `naslov-in-thumbnail`, `ideje-za-klipe`, `pravila-kko` (navajaj ID pravil), `epizoda`, `razlagalne-animacije`, `zupnikm`.

## Uredniška pravila (izmerjeno na kanalu)
- Trditev v naslovu > vprašanje (CTR 6,8 % vs 5,6 %). Naslovi slovenski, več variant za A/B; tekst na thumbnailu dopolni naslov.
- Polovica gledalcev odide pri ~124 s. Delitve so edini napovednik dosega.
- Niso dejavniki: dolžina naslova, ime gosta v naslovu, št. besed na thumbnailu, obrazi, št. poglavij.
- Ton: brez posmehovanja. `potrjeno` = izmerjeno, `dogovor` = Tobijeva odločitev, `ovrzeno/umaknjeno` = nikoli ne uporabi. Tobijevi popravki niso »potrjeno«.
- Podnapisi: 2 vrstici, 3–5 besed; voditelj gostu vika, sicer tikanje; glosar katoliških izrazov.

## Premiere Pro (MCP)
- Klip: `set_sequence_in_out_points` → `create_subsequence` (ignoreTrackTargeting) → »…_Sub_01« → preimenuj → `clear_sequence_in_out`. `unnest_sequence` je pokvarjen.
- Markerji prek `execute_extendscript` (`projectItem.getMarkers()`).
- Sekvence 23,976 fps. Prepis je lahko zamaknjen.
- Podnapisi se ne vidijo → Sequence → Captions → Hide All Caption Tracks.
- Črno ozadje animacij: Blend Mode Screen.

## Narejeno
- Pine (Fr. Gregory Pine): opis, 10+ klipov, naslovi, trailer, SLO podnapisi.
- Aleš Ugovšek (Med ovcami 22): prepis, opis SLO/ANG, 19 poglavij, 5 naslovov, animacija »drevo«.
- Druga epizoda: 26 klipov iz markerjev → vertikalno 1080×1920, avtomatsko menjavanje kadrov.
- Animacije R03, R04, CTA komentar. **Animacije nikoli brez besedila na ekranu** (napis doda Tobija).
- Matjaž Celarc (Med ovcami 24, biblicist, posneto 29. 9.): prepis z MLX, `objava_matjaz.md`, `klipi_matjaz.md`, `klipi_premiere.json` (37 markerjev → 29 klipov, 18 nad pragom).
- MLX z `hallucination_silence_threshold` izpušča ~30 s govora: vrzeli >8 s vedno prepiši posebej.

## Odprto
- Matjaž: preveri točke v `objava_matjaz.md` (13 let v Rimu?, vloga, »obavtna«, vrzel 1:23:26).
- Odločitev o Ollama popravkih.
- Spletna trgovina KKO (PrestaShop), jesen 2026.
