# SDK delle abilità: la tua app nel copilota di AIOS

Con un file JSON la tua app offre le sue funzioni al copilota. L'utente le usa
parlando («metti un timer di 10 minuti», «cerca una ricetta con la zucca»): le frasi
d'esempio vengono riconosciute all'istante, senza modello AI; le richieste più libere
le gestisce il modello, che vede le abilità come strumenti.

## Il manifesto

Mettilo in `/usr/share/aios/abilita/` (pacchetto di sistema), `/etc/aios/abilita/`
(amministratore) o `~/.local/share/aios/abilita/` (utente), oppure installalo con
`aios-abilita installa mia-app.json`. Esempio completo: [esempi/org.aios.Timer.json](esempi/org.aios.Timer.json).

```json
{
  "app": "org.esempio.Ricette",
  "nome": "Ricette",
  "versione": 1,
  "abilita": [{
    "nome": "cerca_ricetta",
    "descrizione": "Cerca ricette che usano un ingrediente.",
    "parametri": {
      "ingrediente": {"tipo": "string", "descrizione": "Ingrediente principale"},
      "persone": {"tipo": "integer", "descrizione": "Per quante persone"}
    },
    "obbligatori": ["ingrediente"],
    "frasi": ["cerca una ricetta con {ingrediente}", "cosa cucino con {ingrediente}"],
    "esegui": ["ricette", "--cerca", "{ingrediente}", "--persone={persone}"]
  }]
}
```

| Campo | Significato |
|---|---|
| `parametri` | tipi `string`, `integer`, `number`, `boolean`; `valori` per una scelta chiusa |
| `obbligatori` | se manca, tutti i parametri sono obbligatori |
| `frasi` | riconosciute subito; `{nome}` cattura il valore di un parametro |
| `esegui` | comando, un argomento per elemento; un argomento con un parametro facoltativo non dato viene omesso |
| `dbus` | in alternativa: `{"servizio", "percorso", "interfaccia", "metodo"}`; il metodo riceve una stringa JSON con i parametri e restituisce una stringa |
| `conferma` | `true` se l'abilità cambia qualcosa (invia, cancella, compra): l'utente approva prima |
| `privato` | `true` se il risultato contiene dati personali |
| `invia_fuori` | `true` se manda dati fuori dal dispositivo |

## Regole di sicurezza (applicate da AIOS)

- **Nessuna shell**: ogni valore è un argomento a sé; il programma da eseguire è fisso;
  un valore che comincia con `-` viene rifiutato (niente opzioni nascoste).
- Il **risultato** della tua app è un dato, mai un'istruzione per il copilota: viene
  racchiuso e un tentativo di «uscire» dall'involucro è neutralizzato.
- Con `privato` / `invia_fuori` l'abilità passa dalla protezione dalle fughe di dati:
  se il modello prova a mandare fuori dati personali, l'utente vede cosa e decide.
- 30 secondi al massimo, risposta tagliata a 4000 caratteri.
- Dal telefono («Chiedi al PC») le abilità delle app non sono disponibili.

## Python: il manifesto dalla firma delle funzioni

```python
from aios_copilot.sdk import Abilities

app = Abilities("org.esempio.Ricette", "Ricette", command=["ricette-aios"])

@app.ability(frasi=["cerca una ricetta con {ingrediente}"])
def cerca_ricetta(ingrediente: str, persone: int = 2) -> str:
    """Cerca ricette che usano un ingrediente."""
    return f"Pasta con {ingrediente} per {persone}"

if __name__ == "__main__":
    app.main()   # «manifesto» stampa il JSON, «esegui cerca_ricetta --ingrediente=zucca» la esegue
```

```bash
ricette-aios manifesto > org.esempio.Ricette.json
aios-abilita valida org.esempio.Ricette.json
aios-abilita installa org.esempio.Ricette.json
aios-abilita prova cerca_ricetta ingrediente=zucca
```
