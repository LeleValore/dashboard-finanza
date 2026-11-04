# Tutor Trader: roadmap del prodotto

Un tutor personale che analizza i mercati, propone operazioni con un piano preciso,
controlla le tue idee prima che tu le esegua e ti fa migliorare analizzando i risultati.
Le decisioni e gli ordini restano sempre tuoi. Il tutor non garantisce guadagni: il suo
compito è darti un vantaggio misurabile e impedirti gli errori che costano di più.

**Come si usa:** lavoriamo un punto alla volta, in ordine. Un punto si spunta solo quando
funziona ed è stato verificato. Una fase si chiude solo se supera il suo controllo.

## Regole del prodotto

- **Il rischio viene prima del guadagno.** Massimo 1% del patrimonio per operazione,
  calo massimo complessivo accettato -25%.
- **Niente entra senza prova.** Un modello resta solo se batte le regole semplici, al
  netto di costi e tasse, su dati che non ha mai visto.
- **Prima il finto, poi il vero.** I soldi veri arrivano solo dopo aver superato la fase 7.
- **Il tutor avvisa, non blocca.** Semaforo verde, giallo o rosso con il rischio spiegato:
  la decisione resta sempre tua, anche nei test, e ogni scelta viene registrata.
- **Ogni suggerimento si spiega.** Motivo, ingresso, stop, obiettivo e cosa lo invaliderebbe.

## Le tre velocità

| Orizzonte | Durata operazione | Strumenti | Quota iniziale |
|---|---|---|---|
| Breve | Da 1 a 10 giorni | Crypto, azioni liquide, ETF | 10% |
| Medio | Da 1 a 6 mesi | Azioni in crescita | 20% |
| Lungo | Anni | ETF ed ETC | 70% |

Il breve è la parte più difficile: non produrrà un guadagno ogni giorno e sarà la prima
a essere spenta se non dimostra un vantaggio.

---

## Fase 0: Fondamenta dati ✅

- [x] 0.1 Storico giornaliero salvato nel repository privato
- [x] 0.2 Prezzi e 13 indicatori di rischio con storico lungo
- [x] 0.3 Archivio PDF delle banche con riconoscimento delle nuove edizioni
- [x] 0.4 View delle banche estratte con citazione esatta
- [x] 0.5 Backtest delle regole base con costi inclusi
- [x] 0.6 Selezione aziende per tema con archivio giornaliero
- [x] 0.7 Portafoglio virtuale da 1.000 € attivo

## Fase 1: Regole e profilo

- [x] 1.1 Profilo di rischio in un unico file di configurazione
- [x] 1.2 Calcolo automatico della dimensione di ogni operazione
- [x] 1.3 Avvisi al raggiungimento della perdita giornaliera, settimanale e mensile massima
- [ ] 1.4 Tasse italiane e commissioni reali nel backtest e nel portafoglio virtuale
- [ ] 1.5 Ripristino del calendario economico con una fonte accessibile
- [x] 1.6 Stato del sistema nel report: ogni fonte con il suo esito
- [x] 1.7 Semaforo da terminale per valutare un'operazione

**Controllo:** il sistema segnala in rosso un'operazione che viola una regola.

## Fase 2: Previsione del rischio

- [ ] 2.1 Previsione della volatilità per ogni strumento (GARCH o HAR)
- [ ] 2.2 Regime di mercato: risk-on, neutrale, risk-off
- [ ] 2.3 Esposizione adattata al regime, confrontata con il nucleo semplice

**Controllo:** la previsione batte la volatilità storica e riduce il calo massimo a parità di rendimento.

## Fase 3: Modelli di segnale

- [ ] 3.1 Dataset di variabili senza informazioni dal futuro
- [ ] 3.2 Etichette a tripla barriera: obiettivo, stop o scadenza
- [ ] 3.3 Regressione multinomiale con validazione walk-forward
- [ ] 3.4 LightGBM come sfidante
- [ ] 3.5 Timestral e modelli simili come sfidanti
- [ ] 3.6 Probabilità calibrate: un 60% deve verificarsi circa 6 volte su 10

**Controllo:** almeno un modello batte le regole semplici fuori campione, al netto dei costi. Se nessuno ci riesce, restano le regole semplici.

## Fase 4: Strategie per orizzonte

- [ ] 4.1 Breve: strategie su crypto e titoli liquidi, con test a 1-10 giorni
- [ ] 4.2 Medio: rotazione mensile delle azioni in crescita
- [ ] 4.3 Lungo: nucleo con ribilanciamento e piano di accumulo
- [ ] 4.4 Allocazione tra i tre orizzonti in base ai risultati

**Controllo:** ogni strategia ha un'aspettativa positiva dopo costi e tasse, altrimenti viene esclusa.

## Fase 5: Il tutor

- [x] 5.1 Interfaccia scelta: sito privato installabile su iPhone e Mac (PWA), con server e login
- [ ] 5.2 Scheda operazione: motivo, ingresso, stop, obiettivo, dimensione, invalidazione
- [ ] 5.3 Controllo delle tue idee: semaforo su un'operazione prima che tu la faccia
- [ ] 5.4 Diario delle operazioni con motivo ed emozioni
- [ ] 5.5 Avvisi su telefono per segnali e stop
- [ ] 5.6 Revisione settimanale con errori ricorrenti e cosa migliorare
- [ ] 5.7 Percorso didattico legato alle tue operazioni reali
- [ ] 5.8 Chat con il tutor su mercati, report e tuo storico

**Controllo:** per una settimana fai tutto il ciclo dal tutor: idea, controllo, diario, revisione.

## Fase 6: Second brain

Il tutor ricorda tutto e migliora con l'esperienza. Ogni lezione si basa sui tuoi dati, non su impressioni.

- [ ] 6.1 Archivio unico di tutte le operazioni, virtuali e reali, dall'idea all'esito
- [ ] 6.2 Fotografia automatica del contesto a ogni operazione: regime, VIX, view, notizie, segnali
- [ ] 6.3 Statistiche per strategia, orizzonte, regime, tipo di errore e colore del semaforo
- [ ] 6.4 Ricerca nella memoria: operazioni simili, report e notizie dello stesso contesto
- [ ] 6.5 Lezioni apprese: il tutor propone nuove regole con le prove, tu le approvi
- [ ] 6.6 Memoria personale: profilo, punti deboli, obiettivi e lezioni usati in ogni consiglio
- [ ] 6.7 Riaddestramento periodico: un nuovo modello sostituisce il vecchio solo se è migliore
- [ ] 6.8 Controllo dei consigli passati: il tutor misura quanto aveva ragione

**Controllo:** alla domanda "cosa è successo l'ultima volta in una situazione simile?" il tutor risponde citando operazioni vere, e ogni regola appresa mostra i numeri che la sostengono.

## Fase 7: Prova con denaro fittizio

- [ ] 7.1 Almeno 3 mesi di prova complessiva con il sistema completo
- [ ] 7.2 Almeno 100 operazioni per valutare la parte a breve
- [ ] 7.3 Confronto mensile tra risultati reali della prova e backtest
- [ ] 7.4 Il calo massimo resta dentro il limite
- [ ] 7.5 Il rendimento netto batte il confronto scelto per ogni orizzonte

**Controllo:** tutti i punti superati. Le parti che falliscono tornano alla fase 3 o 4, oppure si spengono.

## Fase 8: Denaro vero, importi piccoli

- [ ] 8.1 Scelta del broker e verifica di costi e regime fiscale
- [ ] 8.2 Verifica fiscale con un commercialista
- [ ] 8.3 Capitale iniziale che puoi permetterti di perdere
- [ ] 8.4 Ordini inseriti da te, mai automatici
- [ ] 8.5 Confronto tra risultati reali e prova per 3 mesi

**Controllo:** i risultati reali sono coerenti con la prova e rispetti le regole.

## Fase 9: Prodotto finito

- [ ] 9.1 Monitoraggio: avviso se dati o modelli smettono di funzionare
- [ ] 9.2 Verifica automatica del degrado delle strategie
- [ ] 9.3 Test automatici sulle parti che gestiscono rischio e denaro
- [ ] 9.4 Manuale d'uso

**Prodotto finito quando:** ogni giorno ricevi dati aggiornati e segnali spiegati, il tutor
controlla le tue idee, ricorda ogni operazione e impara dai risultati, il rischio è sotto controllo e i risultati sono
misurati onestamente contro i confronti.

---

## Miglioramenti futuri

- Dati a pagamento: bilanci storici, dati intraday, fonti di news
- Più banche e più lingue nei report
- Strategie su opzioni, solo dopo aver dimostrato disciplina sul resto
- Collegamento diretto al broker con conferma manuale
- Apertura ad altri utenti, solo dopo una verifica legale: in Italia la consulenza finanziaria è regolamentata
