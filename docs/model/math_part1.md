# 3. Methodik und Systemarchitektur

## 3.1 Ausgangslage und methodischer Zugang

Kapitel 1 hat die Lücke präzise benannt: Die Transport- und Semantikschicht für vernetzte Alarmsysteme steht mit ISO/IEEE 11073 SDC bereit, doch die konkrete Entscheidungslogik auf Ebene des Smart Alerting Systems (SAS) trägt in IHE SDPi weiterhin den Status `(tba)` [20]. Jede Lösung, die diese Lücke schließen will, muss vier Anforderungen gleichzeitig erfüllen, die sich unmittelbar aus §1.3 und §1.4 ergeben:

* Sie muss auf der Integrator-Ebene ansetzen und ein vollständiges Entscheidungsmodell liefern, nicht nur eine weitere Heuristik.
* Sie muss deterministisch und schrittweise nachvollziehbar sein — §1.3, Punkt 2, benennt explizit die mangelnde Verifizierbarkeit datengetriebener Black-Box-Modelle als eines der beiden zentralen Hindernisse.
* Sie muss mit Quellen unterschiedlicher diagnostischer Güte umgehen können, statt Kanäle als austauschbar zu behandeln.
* Sie muss auf dem aufbauen, was SDC bereits bereitstellt, statt eine parallele Infrastruktur zu entwerfen.

Der entscheidende Schritt liegt in einer Beobachtung, die einfach klingt, aber die gesamte Modellbildung trägt: Eine Alarmbedingung ist, auf ihre logische Struktur reduziert, ein binäres Signal im Zeitverlauf — aktiv oder inaktiv. Was SDC gegenüber der isolierten Gerätewelt aus Kapitel 1 neu leistet, ist, dieses Signal erstmals geräteübergreifend maschinenlesbar und in standardisierter Form verfügbar zu machen: mit Quellenkennung (`<pm:Source>`), Priorität und exakten Zeitpunkten von Entstehung und Beendigung [19]. Damit hört das Problem auf, ein Sonderfall der klinischen Informatik zu sein, und wird zu einer Instanz eines Problems, das in der Signalverarbeitung und der Entscheidungsfusionstheorie seit Jahrzehnten formal untersucht wird: der Fusion mehrerer heterogener, unabhängiger Detektoren zu einer gemeinsamen Entscheidung.

Aus dieser Übersetzung ergibt sich der Argumentationsweg des Kapitels. Die Fusionsregel selbst wird aus der klassischen Theorie der dezentralen Detektion abgeleitet, ausgehend von einer bereits normativ anerkannten Basislogik (3.2). Da diese Theorie auf Entscheidungen zu einem festen Zeitpunkt aufbaut, während klinische Ereignisse unterschiedlich lange andauern, wird vor der eigentlichen Modellbildung geklärt, was die benötigten Kenngrößen in kontinuierlicher Zeit überhaupt bedeuten und weshalb sie als Wahrscheinlichkeiten interpretiert werden dürfen (3.3). Erst danach wird das Modell selbst entfaltet (3.4), wobei zwei weitere Resultate an exakt der Stelle eingeführt werden, an der sie gebraucht werden: die sequenzielle Testtheorie bei der Frage, wie Evidenz über die Zeit akkumuliert wird (3.4.3), und eine Konstruktion aus der Radarsignalverarbeitung bei der Frage, wie eine Entscheidungsschwelle sich an wechselnde Bedingungen anpassen kann (3.4.5). Die Bedingungen, unter denen jede Formel wohldefiniert ist, sowie die unmittelbar aus den Formeln folgenden Grenzen des Modells werden jeweils an der Stelle benannt, an der sie entstehen; ihre Diskussion im Licht der Evaluationsergebnisse erfolgt in Kapitel 5 und 6.

---

## 3.2 Der klassische Ausgangspunkt: k-aus-N und gewichtete Fusion

Der naheliegendste Ausgangspunkt zur Fusion mehrerer unabhängiger Alarmquellen ist die klassische **k-aus-N-Redundanzlogik** der Zuverlässigkeitstheorie [25]. Ein System dieser Klasse löst genau dann aus, wenn mindestens $k$ von $N$ unabhängigen, binären Indikatoren aktiv sind:

$$
\text{Alarm} \iff \sum_{j=1}^{N} \mathbb{1}[a_j = 1] \ge k
$$

Diese Logik ist kein beliebiger Startpunkt: Mehrheitsentscheidungen über redundante Kanäle sind ein anerkanntes Prinzip sicherheitskritischer Systeme, und die in Kapitel 2 (§2.1.3) behandelte Alarmnorm erkennt mehrparametrische Plausibilisierung in intelligenten Alarmsystemen konzeptionell an [21]. Sie weist jedoch drei strukturelle Grenzen auf:

1. Sie setzt **statistisch gleichwertige** Kanäle voraus.
2. Der Schwellenwert $k$ ist **statisch**, unabhängig vom Schweregrad der beteiligten Signale.
3. Sie kennt keinen Mechanismus zur Einbindung **klinischen Kontextwissens**.

### Chair-Varshney als Antwort auf die erste Grenze

Chair und Varshney zeigten 1986, dass bei bekannten kanalspezifischen Fehlerraten die optimale Fusion binärer Sensorentscheidungen durch eine gewichtete Summe logarithmierter Likelihood-Quotienten erfolgt [26]. Jedem Sensor sind dabei zwei Gewichte zugeordnet — eines für die positive, eines für die negative Einzelentscheidung. Das Gewicht der positiven Entscheidung ($a_j = 1$), das für das vorliegende Modell maßgeblich ist, lautet:

$$
w_j = \ln\left(\frac{TPR_j}{FPR_j}\right)
$$

Die Rolle des zweiten, negativen Gewichts wird in 3.4.2 aufgegriffen.

### Formaler Nachweis der Rückwärtskompatibilität

Die k-aus-N-Logik ist kein verworfener Sonderfall, sondern folgt aus dieser Regel bei homogenen Kanälen. Betrachtet werde der stationäre Grenzfall, in dem alle aktiven Quellen ihr Beobachtungsfenster vollständig belegen ($s_j = 1$ für alle $j \in F$, vgl. 3.3), die Schwelle unskaliert ist ($\delta = 1$, vgl. 3.4.5) und kein Kontextbeitrag vorliegt ($Context\_Log\_Odds = 0$, vgl. 3.4.1). Sind dann alle zugelassenen Gewichte gleich, $w_j = w > 0$ für alle $j$, und bezeichnet $F$ die Menge der momentan aktiven Quellen, so reduziert sich die Fusionsbedingung $\sum_{j \in F} w_j \ge k_{min} \cdot \bar{w}$ (mit $\bar w = w$ im homogenen Fall) auf eine reine Kardinalitätsbedingung:

$$
\sum_{j \in F} w_j = |F| \cdot w \;\ge\; k_{min} \cdot w \quad\Longleftrightarrow\quad |F| \ge k_{min}
$$

da $w > 0$. Die k-aus-N-Regel ist damit der Grenzfall der gewichteten Fusion bei Gleichgewichtung — nicht ihre Alternative.

### Die dreistufige Fusionstopologie

Die Theorie der dezentralen Detektion beschreibt Fusionsarchitekturen üblicherweise als hierarchische Topologie aus drei Ebenen — lokale Sensorknoten, lokale Fusionsknoten und ein globales Fusionszentrum [31]. Diese Struktur ist isomorph zur Architektur der vorliegenden Arbeit:

```
Ebene 1                     Ebene 2                        Ebene 3
Sensorknoten                Knoten-Aggregator              Zentralstation
(AlertCondition je Quelle)  (Bettplatz-Ensemble M)         (Anzeige und Quittierung je Bettplatz)

  a_1(t) ⎫
  a_2(t) ⎬──  w_j, s_j(t)  ──►  E(t) = Σ_{j∈M} w_j·s_j(t)  ──Verdikt (Rot/Gelb)──►  Anzeige je Bettplatz
  a_j(t) ⎭                       vs.  Θ_current(t)               (keine Fusion über Betten hinweg)

                              [BICEPS/SDC: Ebene 1 spezifiziert [19]]
                              [IHE SDPi Tab. 3:8.3.2.6-1: Ebene 2 — SAS — als (tba) ausgewiesen [20]]
                              [IHE SDPi-A: Ebene 3 (Transport, Fernquittierung) spezifiziert [20];
                               CDAS-Anforderungen nach IEC 60601-1-8 [21]]
```

*Abbildung 3.1 — Eigene Darstellung der dreistufigen Fusionstopologie in der Notation dieses Modells, nach dem Architekturprinzip hierarchischer dezentraler Detektion aus [31].*

Die Alarmbedingung einer einzelnen Quelle entspricht dem Sensorknoten, das bettseitige Ensemble $M$ dem lokalen Aggregatorknoten, die Zentralstation dem globalen Fusionszentrum. Damit lässt sich präzise benennen, welche Ebene der Standard tatsächlich offenlässt: Ebene 1 ist durch das BICEPS-Alarmmodell spezifiziert [19], Ebene 3 durch das Transportprofil für Fernquittierung (IHE SDPi-A) [20] in Verbindung mit den CDAS-Anforderungen der Alarmnorm [21]. Exakt Ebene 2 — der Knoten-Aggregator — ist die Stelle, die IHE SDPi als Smart Alerting System mit `(tba)` kennzeichnet [20]. Die dreistufige Topologie liefert damit nicht nur eine Analogie, sondern ein strukturelles Argument dafür, wo diese Arbeit ansetzt.

Zu beachten ist eine Asymmetrie gegenüber der klassischen Topologie: Dort fusioniert das globale Fusionszentrum die Entscheidungen mehrerer lokaler Aggregatoren. Hier erhält die Zentralstation dagegen bereits ein fertiges Verdikt je Bettplatz und fusioniert die Betten untereinander nicht. Die dritte Ebene ist topologisch vorhanden, führt in dieser Architektur jedoch keine Fusion aus; sie ist in Abbildung 3.1 deshalb als Anzeige- und Quittierungsinstanz bezeichnet. Eine Fusion auf Stationsebene — dort, wo Betten über eine gemeinsame Ursache korrelieren (Stromausfall, Netzwerkausfall) — wäre eine natürliche Erweiterung dieser Architektur und bleibt zukünftiger Arbeit vorbehalten.

Offen bleiben an dieser Stelle die zweite und dritte strukturelle Grenze der k-aus-N-Logik — der statische Schwellenwert und das fehlende Kontextmodell. Sie werden in 3.4.5 beziehungsweise 3.4.1 aufgelöst.

---

## 3.3 Vom Ereignis zur Belegung: die zeitliche Grundlage

Die Chair-Varshney-Regel operiert auf Entscheidungen zu einem festen Zeitpunkt. Klinisches Monitoring liefert jedoch Ereignisse unterschiedlicher Dauer — eine Alarmbedingung ist über ein Zeitintervall aktiv, nicht in einem Augenblick. Bevor das Modell formuliert werden kann, muss daher geklärt werden, was $TPR_j$ und $FPR_j$ in kontinuierlicher Zeit bedeuten.

Jede Alarmquelle $j$ wird durch zwei bedingte Wahrscheinlichkeiten charakterisiert, wobei $a_j(t) \in \{0,1\}$ den binären Alarmzustand (Präsenz der Alarmbedingung gemäß BICEPS `AlertConditionState/@Presence` [19]) und $C$ das Ereignis einer systemischen physiologischen Krise des überwachten Patienten bezeichnet:

$$
TPR_j = P\big(a_j(t) = 1 \mid C\big), \qquad FPR_j = P\big(a_j(t) = 1 \mid \neg C\big)
$$

**Warum auf den Patientenzustand bedingt wird, nicht auf das Auftreten eines Alarms.** Dem Ereignis „die Quelle hat ausgelöst" steht kein Gegenstück „die Quelle hätte auslösen können, tat es aber nicht" gegenüber, das gezählt werden könnte. Eine Bedingung auf das Alarmereignis selbst hätte damit keinen wohldefinierten Nenner. Die Bedingung auf den Patientenzustand liefert diesen Nenner: Innerhalb eines Krisenintervalls ist genau definiert, welchen Zeitanteil die Quelle aktiv war, unabhängig davon, wie oft sie im Detail ausgelöst hat.

Damit bleiben zwei getrennte Fragen zu beantworten: Woher stammt der Zahlenwert, und weshalb darf er als Wahrscheinlichkeit in eine Bayes'sche Rechnung eingesetzt werden? Beide Fragen werden durch zwei unterschiedliche, hier explizit benannte Resultate beantwortet.

### Erste Brücke — Messbarkeit: die Formel von Little in Auslastungsform

Sei $\nu_j$ die Rate der Alarmbeginne (Übergänge $a_j: 0 \to 1$) von Quelle $j$ außerhalb einer Krise, gemessen in Aktivierungen pro Zeiteinheit, und $\bar d_j$ deren mittlere Dauer. In der Auslastungsform der Formel von Little entspricht der im langfristigen Mittel von einer Quelle „belegte" Zeitanteil dem Produkt aus Ereignisrate und mittlerer Ereignisdauer [32]:

$$
FPR_j = \nu_j \cdot \bar d_j
$$

mit der analogen Beziehung für $TPR_j$ aus Rate und mittlerer Dauer der Aktivierungen innerhalb von Krisenintervallen.

Die Anwendbarkeit dieser Form ist unmittelbar gegeben: Ein einzelner Kanal ist ein einkanaliges System ohne Warteschlange, da $a_j$ binär ist und sich Alarme auf ein und demselben Kanal nicht überlappen können; die mittlere Anzahl im System fällt damit mit $P(a_j = 1)$ zusammen, und die Auslastung entspricht genau $\nu_j \cdot \bar d_j$. Vorausgesetzt ist dabei die Stationarität des Alarmprozesses innerhalb des jeweiligen Zustands ($C$ bzw. $\neg C$), auf die im Folgenden zurückgekommen wird.

Diese Beziehung beantwortet die Frage der Messbarkeit: Rate und Dauer sind unmittelbar aus SDC-Alarmzustandsprotokollen rekonstruierbar, die Beginn und Ende jeder Alarmbedingung protokollieren [19]. Ohne diese Brücke wäre unklar, woher $TPR_j$ und $FPR_j$ überhaupt kommen sollen.

### Die Belegungsfunktion $s_j(t)$

Um diskrete Impulse in ein robustes, zeitgewichtetes kontinuierliches Maß zu überführen, wird eine zeitliche Integration über ein gleitendes Fenster der Dauer $T\,[\text{s}]$, $T > 0$, angewendet. Die Quellenbelegungsfunktion $s_j(t) \in [0,1]$ ist der kontinuierliche gleitende Mittelwert des ZOH-rekonstruierten Signals:

$$
s_j(t) = \frac{1}{T} \int_{t-T}^{t} a_j(\tau) \, d\tau
$$

*(Konvention: Zur formalen Auflösung des Integralzustands vor der Systeminitialisierung wird $a_j(\tau) = 0$ für alle $\tau < t_0$ angenommen, wobei $t_0$ den Beginn der Überwachung bezeichnet.)*

### Zweite Brücke — Interpretierbarkeit: der Ergodensatz von Birkhoff–Chintschin

Die erste Brücke erklärt, woher der Zahlenwert stammt; sie erklärt nicht, weshalb ein gleitendes **Zeitmittel** legitim als **Wahrscheinlichkeit** in eine Bayes'sche Rechnung eingesetzt werden darf. Hier sind zwei unterschiedliche Aussagen sauber zu trennen.

Die Gleichheit der Erwartungswerte,

$$
\mathbb{E}[s_j(t) \mid C] = TPR_j, \qquad \mathbb{E}[s_j(t) \mid \neg C] = FPR_j,
$$

folgt bereits aus der **Stationarität** allein, durch Linearität des Erwartungswerts über dem Integral, und gilt für **jedes endliche** $T$ — nicht erst im Grenzwert. Stationarität stellt lediglich sicher, dass $P(a_j(\tau) = 1 \mid C)$ nicht von $\tau$ abhängt, sodass der Erwartungswert des Zeitmittels mit $TPR_j$ zusammenfällt.

Was die Stationarität allein nicht liefert, ist eine Aussage über die **tatsächlich beobachtete** Größe selbst — also über die eine Realisierung, die aus dem Alarmprotokoll einer Station berechnet wird, nicht über ihren Erwartungswert. Genau hier wird die **Ergodizität** benötigt. Sei $\tau_{obs}$ die Länge des zur Kalibrierung herangezogenen Beobachtungszeitraums; dieser ist strikt vom Modellfenster $T$ zu unterscheiden, das im Betrieb kurz bleibt. Ist der Prozess $a_j(\tau)$ bedingt auf $C$ (bzw. $\neg C$) stationär und ergodisch, so konvergiert das über $\tau_{obs}$ gebildete Zeitmittel einer einzelnen Realisierung für $\tau_{obs} \to \infty$ fast sicher gegen den bedingten Erwartungswert des Prozesses [30, 33]. Dies — nicht die Gleichheit der Erwartungswerte — ist der eigentliche Grund, weshalb der Ergodensatz hier herangezogen wird: Er rechtfertigt, $TPR_j$ und $FPR_j$ anhand des Alarmprotokolls **einer einzigen** Station zu kalibrieren, also anhand einer einzigen Trajektorie, statt ein Ensemble unabhängiger Wiederholungen zu benötigen.

Beide Brücken beantworten damit unterschiedliche Fragen — die eine die Messbarkeit, die andere die Rechtfertigung der Kalibrierung an einer einzigen Trajektorie —, und erst gemeinsam rechtfertigen sie, $TPR_j$ und $FPR_j$ sowohl als beobachtbare Kenngrößen als auch als aus einem einzelnen Stationsprotokoll schätzbare Wahrscheinlichkeiten zu behandeln.

### Brücke zur klinischen Kennzahl: Bayes in Chancenform

Der diagnostische Wert von $w_j$ lässt sich unmittelbar mit dem positiven Vorhersagewert (PPV) verknüpfen. Aus dem Satz von Bayes folgt für die Chancen (*Odds*) einer Krise gegeben eine aktive Quelle $j$:

$$
\text{Odds}(C \mid a_j{=}1) = \frac{TPR_j \cdot P(C)}{FPR_j \cdot (1-P(C))} = e^{w_j} \cdot \frac{P(C)}{1-P(C)}
$$

woraus mit $\text{PPV} = P(C \mid a_j{=}1)$ und $\text{FDR} = 1-\text{PPV}$ folgt:

$$
\frac{\text{PPV}}{1-\text{PPV}} = \frac{P(C)}{1-P(C)} \cdot e^{w_j}, \qquad \text{FDR} = 1 - \text{PPV}
$$

Drei Folgerungen ergeben sich unmittelbar:

1. $w_j$ ist genau der Logarithmus des Likelihood-Faktors im Bayes'schen Chancen-Update. Dies ist die formale Rechtfertigung dafür, dass unabhängige Kanalbeiträge im Log-Odds-Raum **additiv** summiert werden dürfen (3.4.3).
2. Der PPV wird durch das Produkt zweier Faktoren bestimmt, von denen nur einer — $e^{w_j}$ — dem Sensor zuzurechnen ist; der andere ist die Prävalenz $P(C)$. Bei niedriger Krisenprävalenz entsteht eine hohe FDR selbst bei einem hochspezifischen Kanal. Die in Kapitel 1 (§1.2.1) zitierte hohe Quote klinisch irrelevanter Alarme (72–99 %) ist damit kein Beleg für schlechte Sensorqualität, sondern eine unmittelbare Konsequenz niedriger Prävalenz — dies bestätigt die in §1.3 getroffene Feststellung.
3. Da der Prävalenzfaktor extern gegeben und vom Algorithmus nicht beeinflussbar ist, bleibt die aggregierte Evidenz $\sum_{j \in F} w_j$ die einzige Größe, auf die ein Filter **auf Integrator-Ebene** Einfluss nehmen kann; Eingriffe in die Einzelgewichte $w_j$ selbst — etwa durch veränderte Alarmgrenzen oder Verzögerungen an der Quelle — liegen auf Geräteebene und außerhalb des hier betrachteten Modells. Die Aggregation mehrerer Kanäle ist damit kein heuristischer Kunstgriff, sondern der auf dieser Ebene einzig verfügbare Hebel.

Diese Verbindung ist rein struktureller Natur. Konkrete Zahlenwerte für $P(C)$, die inverse Kalibrierungsformel, die Unterscheidung zwischen episodenbasierter und belegungsbasierter FDR sowie die Alarmreduktionsrate (ARR) sind Gegenstand von Kapitel 5. Festzuhalten ist an dieser Stelle nur, dass $P(C)$ ein struktureller Parameter ist und nicht mit dem algorithmischen Prior $P_0$ aus 3.4.1 identisch ist.

---

## 3.4 Das Modell

### 3.4.1 Klinischer Kontext (Prior)

Vor Eintreffen jeglicher Echtzeitsignale besitzt der Patient ein Basisrisiko. Sei $P_0 \in (0,1)$ die algorithmische Prior-Wahrscheinlichkeit für die Entwicklung einer Krise; die offenen Intervallgrenzen sind erforderlich, damit die nachfolgende logarithmische Transformation definiert und endlich bleibt. Da Wahrscheinlichkeiten in der Nähe der Grenzen $[0,1]$ nicht ohne nichtlineare Verzerrung additiv verknüpft werden können, wird $P_0$ in Basis-Odds überführt:

$$
O_0 = \frac{P_0}{1 - P_0}
$$

Das System individualisiert dieses Risiko anhand elektronischer Patientenakten (EHR) über HL7-FHIR-Schnittstellen. Der klinische Zustand wird als endliche Menge aktiver Diagnosen und Komorbiditäten $D = \{d_1, \dots, d_m\}$ formalisiert. Jedem Diagnosecode $d \in D$ ist ein Odds Ratio $OR_d > 0$ zugeordnet, das angibt, um welchen Faktor die Diagnose die *Chancen* (nicht die Wahrscheinlichkeit) der Krise $C$ verändert; Werte $OR_d > 1$ erhöhen, Werte $OR_d < 1$ senken das Risiko. Für Diagnosen ohne hinterlegtes Odds Ratio wird $OR_d = 1$ gesetzt, sodass sie den Prior nicht verändern.

Eine Diagnose beeinflusst die Eskalationslogik nur, wenn das aktuell zugelassene Sensor-Ensemble $M$ über Parameter verfügt, die physiologisch mit ihr in Zusammenhang stehen. Hierzu wird eine binäre Relevanzindikatorfunktion eingeführt, die auf einer konfigurierten Zuordnungstabelle zwischen Diagnosecodes und überwachten Messgrößen beruht:

$$
R_d(M) = \begin{cases} 1, & \text{falls Ensemble } M \text{ mit } d \text{ assoziierte Parameter überwacht} \\ 0, & \text{sonst} \end{cases}
$$

Die individualisierten Prior-Odds ergeben sich aus der Multiplikation der Basis-Odds mit den Gewichten aller relevanten Diagnosen:

$$
O_{prior} = O_0 \cdot \prod_{d \in D} (OR_d)^{R_d(M)}
$$

Die multiplikative Verknüpfung setzt voraus, dass die Diagnosen hinsichtlich ihres Einflusses auf $C$ voneinander unabhängig sind; bei stark korrelierten Komorbiditäten überschätzt sie den Kontextbeitrag.

Logarithmische Transformation überführt dies in den Log-Odds-Raum und wandelt das Produkt in eine Summe um:

$$
Context\_Log\_Odds = \ln(O_0) + \sum_{d \in D} R_d(M) \cdot \ln(OR_d)
$$

*(Anmerkung: Ist die EHR-Historie des Patienten nicht verfügbar, nimmt der Algorithmus konservativ maximale Entropie an und setzt $P_0 = 0{,}5$. Dies führt zu $Context\_Log\_Odds = 0$ und neutralisiert den Bias vollständig. Dieser algorithmische Prior ist strikt von der empirischen Krisenprävalenz $P(C)$ aus 3.3 zu unterscheiden, die in Kapitel 5 zur externen Belastungsauswertung verwendet wird.)*

### 3.4.2 Alarmquellen, diagnostisches Gewicht und Definitionsbereich des Ensembles

Mit $TPR_j$, $FPR_j$ und $s_j(t)$ aus 3.3 lässt sich das klinische Diagnosegewicht der Quelle $j$ bilden:

$$
w_j = \ln\left(\frac{TPR_j}{FPR_j}\right)
$$

**Strikte Gateway-Zulassungsregel.** Um zu verhindern, dass uninformative oder fehlerhafte Sensoren das System mathematisch untergraben, werden nur Quellen mit strikt positivem und beschränktem Diagnosegewicht in das aktive Überwachungs-Ensemble $M$ aufgenommen:

$$
0 < FPR_j < TPR_j \le 1
$$

Dies garantiert inhärent $w_j \in (0, \infty)$ und verhindert unendliche Gewichte bei empirischem $FPR_j = 0$. Quellen, die diese Bedingung verletzen ($w_j \le 0$ oder $FPR_j = 0$), werden von sämtlichen nachfolgenden Rechenschritten ausgeschlossen; ihre Annunziation verbleibt vollständig beim lokalen Bettplatzgerät.

**Mindestgröße des Ensembles.** Das Modell ist für Ensembles mit $|M| \ge 2$ definiert. Diese Untergrenze ist keine Randbedingung der Implementierung, sondern Teil des Definitionsbereichs: Das dynamische Quorum $k_{min}$ (3.4.4) ist als Kreuzvalidierung zwischen mindestens zwei Quellen konstruiert und verliert bei einer einzelnen Quelle seine Semantik. Solange für einen Bettplatz weniger als zwei Quellen zugelassen sind, wird keine zentrale Eskalation durch dieses Modell erzeugt; die Alarmierung erfolgt ausschließlich lokal über die Bettplatzgeräte (Fail-Safe). Im Rahmen des hier evaluierten Proof-of-Concept wird zudem ein konstantes, vor Beginn der Überwachung zugelassenes Ensemble vorausgesetzt; Topologieänderungen im laufenden Betrieb (*Hot-Plug*) sind Gegenstand zukünftiger Arbeit.

*Anmerkung zur Fusionsheuristik.* Die in 3.2 hergeleitete Chair-Varshney-Regel fordert mathematisch die Addition negativer Evidenz für stille Sensoren. Das vorliegende Modell verwendet bewusst eine **Positive-Evidence-Only-Heuristik**, die diese Addition auslässt. Dies verhindert, dass die Evidenz $E(t)$ bei stark instrumentierten, aber stabilen Patienten auf tief negative, nicht rückführbare Werte getrieben wird. Der Entwurfsentscheid erhält die Fail-Safe-Eskalationsfähigkeit, führt jedoch zu einem Verlust an erreichbarer Spezifität gegenüber der optimalen Chair-Varshney-Schranke.

*Annahme der bedingten Unabhängigkeit.* Die Summation der Evidenz setzt bedingte Unabhängigkeit der Kanäle voraus (Naive-Bayes-Annahme). Diese überschätzt die aggregierte Evidenz bei stark korrelierten Mehrkanal-Krisen (schützender Fail-Safe-Effekt) ebenso wie bei korrelierten Artefakten, etwa Patientenbewegung (gefährdender Effekt). Die Wirkung dieses Kompromisses auf die Belastungskennzahlen wird in Kapitel 5 quantifiziert.

### 3.4.3 Evidenzakkumulation

Die gesamte akkumulierte Evidenz im Log-Odds-Raum ergibt sich aus der Summe der gewichteten Kanalbeiträge:

$$
E(t) = \sum_{j \in M} w_j \cdot s_j(t)
$$

Nach Konstruktion gilt $E(t) \in \big[0, \sum_{j \in M} w_j\big]$, und $E(t) = 0$ genau dann, wenn alle zugelassenen Quellen über das gesamte zurückliegende Fenster $[t-T, t]$ inaktiv waren.

**Theoretische Einordnung.** Chair-Varshney liefert eine optimale Fusionsregel für eine Entscheidung zu einem festen Zeitpunkt. Das vorliegende Problem verlangt jedoch eine fortlaufende Akkumulation von Evidenz bis zu einem a priori unbekannten Entscheidungszeitpunkt — strukturell die Aufgabe des **sequenziellen Wahrscheinlichkeitsquotiententests** (SPRT) nach Wald [27]. Wald und Wolfowitz zeigten, dass der SPRT unter gegebenen Fehlerraten die im Erwartungswert minimale Anzahl an Beobachtungen bis zur Entscheidung benötigt [28] — ein Optimalitätsresultat, das die sequenzielle Akkumulation gegenüber einer Entscheidung nach festem Beobachtungsfenster motiviert.

Drei Unterschiede zum klassischen SPRT sind explizit zu benennen:

1. **Gedächtnis.** Der SPRT akkumuliert alle Beobachtungen seit Testbeginn ohne Vergessen. $E(t)$ ist dagegen auf einem gleitenden Fenster der Länge $T$ aufgebaut und besitzt ein begrenztes Gedächtnis — der Beitrag eines Ereignisses erlischt nach $T$ Sekunden.
2. **Zustandsraum der Entscheidung.** Der SPRT besitzt zwei Entscheidungsgrenzen. Das vorliegende Modell besitzt eine Grenze sowie einen darunterliegenden Zwischenzustand (Gelb, 3.4.6).
3. **Adaptivität der Grenzen.** Der SPRT setzt feste, im Voraus berechnete Entscheidungsgrenzen voraus. Hier ist die Schwelle hingegen adaptiv (3.4.5).

Die formale Optimalität von Wald-Wolfowitz überträgt sich damit **nicht** auf das adaptive Modell und wird in dieser Arbeit nicht beansprucht. Die Verbindung ist, wie bei CFAR in 3.4.5, als **Analogie** zu kennzeichnen, nicht als Herleitung.

### 3.4.4 Schweregradindex und dynamisches Quorum

Die Architektur nutzt die IEEE-11073-SDC-Alarmprioritätsstruktur ($P_j \in \{0,1,2,3\}$, entsprechend den BICEPS-Prioritätsstufen `None`, `Lo`, `Me`, `Hi` [19]). Die momentane Bedrohungsgröße ist $v_j(t) = P_j \cdot a_j(t)$. Die klinische Bedrohungsdichte des Ensembles wird über den normierten dynamischen Schweregradindex quantifiziert:

$$
SDC_{score}(t) = \alpha \cdot \left( \frac{\max_{j \in M}(v_j(t))}{\max(1, P_{max,M})} \right) + (1-\alpha) \cdot \left( \frac{\sum_{j \in M} v_j(t)}{\max(1, \sum_{j \in M} P_j)} \right)
$$

wobei $P_{max,M} = \max_{j\in M}(P_j)$ und $\alpha \in [0,1]$ ein Gewichtungsparameter ist. Beide Summanden liegen nach Konstruktion in $[0,1]$ — der erste, weil $\max_j v_j \le P_{max,M}$, der zweite, weil $\sum_j v_j \le \sum_j P_j$ —, und der Index ist deren Konvexkombination; damit gilt $SDC_{score}(t) \in [0,1]$. Die Maximumsbildung im Nenner sichert die Definiertheit für Ensembles, deren Quellen sämtlich die Priorität $0$ tragen.

*Anmerkung.* Der Index operiert auf dem momentanen Rohsignal $a_j(t)$ und nicht auf der gefensterten Belegung $s_j(t)$. Kurzzeitige Artefakte auf hochprioren Kanälen schlagen deshalb unmittelbar auf $SDC_{score}$ durch und können das nachfolgend definierte Quorum und damit die angewendete Schwelle vorübergehend senken.

Diese Architektur überführt die statische k-aus-N-Logik aus 3.2 in ein **dynamisches, schweregradgesteuertes Quorum**:

$$
k_{min}(t) = \text{round}\Big(|M| - (|M|-2) \cdot SDC_{score}(t)\Big)
$$

*(Konvention: Rundung erfolgt kaufmännisch (half-up) zur deterministischen Reproduzierbarkeit.)*

Für $|M| \ge 2$ (3.4.2) ist der Koeffizient $(|M|-2)$ nichtnegativ, sodass der Ausdruck $|M| - (|M|-2)\,\sigma$ für $\sigma \in [0,1]$ monoton fallend das Intervall $[2, |M|]$ durchläuft, dessen Endpunkte ganzzahlig sind. Kaufmännisches Runden einer Zahl aus diesem Intervall liefert stets eine ganze Zahl aus $\{2, \dots, |M|\}$; es gilt daher $k_{min}(t) \in \{2, \dots, |M|\}$ für alle $t$. Bei maximaler Bedrohungsdichte ($SDC_{score} = 1$) sinkt das Quorum auf seinen Boden $2$, bei verschwindender Bedrohungsdichte ($SDC_{score} = 0$) verlangt es die Beteiligung des gesamten Ensembles.

Die Basis-Energieschwelle ergibt sich aus dem durchschnittlichen Diagnosegewicht des Ensembles:

$$
\Theta_{base}(t) = k_{min}(t) \cdot \bar{w}, \qquad \bar w = \frac{1}{|M|}\sum_{j\in M} w_j
$$

Aus $w_j > 0$ folgt $\bar w > 0$ und damit $\Theta_{base}(t) \in [2\bar w, |M|\bar w]$. Das Quorum $k_{min}$ skaliert die geforderte Evidenz*energie*, begrenzt aber nicht die *Kardinalität* der beteiligten Quellen: Die Eskalationsbedingung (3.4.6) ist eine Bedingung an $E(t)$, nicht an die Anzahl aktiver Kanäle. Welche Konsequenz dies für Einzelquellen hat, wird in 3.4.5 nach Einführung der Schwellenskalierung benannt.

### 3.4.5 Schwellenbildung

**Theoretische Einordnung.** Eine feste Entscheidungsschwelle ist bei variablem Hintergrundrauschen strukturell unterlegen. In der Radarsignalverarbeitung wird dieses Problem durch **CFAR-Detektoren** gelöst, deren Schwelle fortlaufend an das lokal geschätzte Rauschniveau angepasst wird [29]. Der folgende Mechanismus überträgt dieses Prinzip **als konstruktive Analogie, nicht als Herleitung** auf das medizinische Monitoring.

Die Ziel-Eskalationsschwelle wird über einen zeitabhängigen Zerfallsmultiplikator $\delta(t)$ und die Kontextverschiebung gebildet:

$$
\Theta_{target}(t) = \max\left(\epsilon(t), \; \Theta_{base}(t)\cdot\delta(t) - Context\_Log\_Odds\right)
$$

Die Reihenfolge ist normativ: $\delta(t)$ skaliert ausschließlich $\Theta_{base}(t)$; die Kontextverschiebung wird danach subtrahiert. Andernfalls würde eine Skalierung der Differenz das Vorzeichen des Mechanismus invertieren, sobald $Context\_Log\_Odds > \Theta_{base}(t)$.

$\epsilon(t) = \min_{j\in M}(w_j)$ ist das minimale Evidenzquantum; aufgrund der Zulassungsregel gilt stets $\epsilon(t) > 0$, und damit $\Theta_{target}(t) > 0$ für alle $t$. Der Boden $\epsilon(t)$ wirkt als absolute Sicherung gegen eine durch positive Kontextverschiebung beliebig abgesenkte Schwelle. Für den neutralen Kontext ($Context\_Log\_Odds = 0$) und $\delta_{min} \ge 0{,}5$ ist er nachweislich nie aktiv, denn dann gilt $\Theta_{base}\,\delta \ge 0{,}5 \cdot 2\bar w = \bar w \ge \min_j w_j = \epsilon$.

Der exponentielle Zerfallsmultiplikator lautet:

$$
\delta(t) = \max\left(\delta_{min}, \; e^{-\lambda(t)\cdot\max(0,\,\Delta t_{active}-T)}\right), \qquad \lambda(t) = \frac{SDC_{score}(t)}{T}
$$

mit einer erforderlichen Untergrenze $\delta_{min} \in (0,1]$ und der wirksamen Evidenzdauer

$$
\Delta t_{active}(t) = \int_{\max(t_0,\; t_{\text{letztes }E=0})}^{t} \big(1 - \mathrm{esc}(\tau)\big)\, d\tau
$$

Darin ist $\mathrm{esc}(\tau) \in \{0,1\}$ das zuletzt *berechnete* Verdikt des Filterkerns ($1$, falls bei der letzten Auswertung vor $\tau$ die Bedingung $E \ge \Theta_{current}$ erfüllt war, sonst $0$), das bis zur nächsten Auswertung konstant gehalten wird. Die untere Integrationsgrenze setzt die Dauer zurück, sobald $E = 0$ erreicht ist; der Integrand hält sie an, solange der Kern eskaliert ist, und schreibt sie fort, sobald die Evidenz unter die angewendete Schwelle gefallen ist. Außerhalb jeder Eskalation reduziert sich der Ausdruck auf $t - \max(t_0, t_{\text{letztes }E=0})$. Aus $\lambda(t) \ge 0$ und dem nichtnegativen Zeitargument folgt $\delta(t) \in [\delta_{min}, 1]$. Innerhalb des ersten Fensters nach Beginn der Evidenz ($\Delta t_{active} \le T$) bleibt der Multiplikator bei $1$; die Schwelle wird erst bei anhaltender, über $T$ hinausgehender Evidenz abgesenkt, und zwar umso schneller, je höher die momentane Bedrohungsdichte ist. Bei $SDC_{score} = 0$ findet keine Absenkung statt.

*Anmerkung zum stationären Verhalten.* $\Delta t_{active}$ wird nur durch $E = 0$ zurückgesetzt, was nach 3.4.3 das Schweigen aller zugelassenen Quellen über ein volles Fenster $T$ voraussetzt. In einer Umgebung mit kontinuierlichem Hintergrundalarmaufkommen ist dies selten; der Multiplikator verharrt dann über lange Zeiträume auf seinem Boden $\delta_{min}$. Dieser Zustand ist deshalb als Regelbetrieb und nicht als Randfall zu betrachten, und die wirksame Mindestschwelle im Regelbetrieb beträgt $\delta_{min} \cdot k_{min}(t) \cdot \bar w$. Anhaltende Evidenz unterhalb dieser Schranke wird niemals zentral eskaliert, sondern verbleibt dauerhaft im lokalen Warnzustand (3.4.6).

**Anhalten von $\Delta t_{active}$ während einer Eskalation.** Der Faktor $(1 - \mathrm{esc}(\tau))$ im Integranden ist ein Entwurfsentscheid, der zwei Zwecken dient. Erstens löst er die andernfalls zirkuläre Abhängigkeit $\delta \to \Theta_{target} \to \Theta_{current} \to \text{Verdikt} \to \Delta t_{active} \to \delta$ auf: Auf dem Intervall zwischen zwei Auswertungen steht im Integranden das Verdikt der *vorhergehenden* Auswertung, sodass $\Delta t_{active}$ und damit $\delta$ zum Auswertungszeitpunkt $t$ nur von bereits berechneten Verdikten abhängen, nicht vom aktuell zu berechnenden. Zweitens verhindert er, dass die Schwelle während einer bereits erklärten Krise weiter abschmilzt und sich nach deren Ende erst aus $\delta_{min}$ erholen müsste, was unmittelbar nach jeder aufgelösten Krise eine unbeabsichtigt erhöhte Empfindlichkeit erzeugen würde.

**Auswertungszeitpunkte.** Die Größen $s_j$, $E$, $SDC_{score}$, $k_{min}$, $\delta$ und $\Theta_{target}$ sind in kontinuierlicher Zeit definiert; ausgewertet werden sie zu den Zeitpunkten $\mathcal{T} = \mathcal{T}_E \cup \mathcal{T}_P$. Dabei bezeichnet $\mathcal{T}_E$ die Zeitpunkte der Alarmereignisse — eine Alarmbedingung wird gesetzt, oder eine beteiligte Bedingung wird beendet — und $\mathcal{T}_P = \{t_0 + k\,\Delta_{eval} : k \in \mathbb{N}\}$ ein periodisches Raster mit $\Delta_{eval} \le T$; der im Prototyp verwendete Wert von $\Delta_{eval}$ wird zusammen mit den übrigen Modellparametern ($T$, $\alpha$, $\delta_{min}$) in Kapitel 4 festgelegt. Das Raster ist notwendig und kein Implementierungsdetail: Eine Quelle trägt in ihrem eigenen Einschaltzeitpunkt nichts bei, da ihr Fenster noch leer ist ($s_j(t_n) = 0$ für $t_n$ den Zeitpunkt ihres Einschaltens, vgl. 3.3). Ohne $\mathcal{T}_P$ würde ein Ensemble, dessen Quellen nahezu gleichzeitig einschalten und danach unverändert aktiv bleiben, nie mit belegtem Fenster ausgewertet und damit nie eskalieren — ein anhaltender Krisenzustand bliebe unsichtbar. Mit $\mathcal{T}_P$ wird jeder anhaltende Zustand spätestens $T + \Delta_{eval}$ nach seinem Beginn mit vollständig belegtem Fenster ausgewertet; ebenso wird das Erreichen von $E = 0$ nach dem Verstummen aller Quellen spätestens nach $\Delta_{eval}$ beobachtet und setzt $\Delta t_{active}$ zurück. Zwischen zwei Auswertungen wird der Zustand nicht neu berechnet und $\Theta_{current}$ auf dem zuletzt berechneten Wert gehalten, wodurch $\Theta_{current}(t)$ als stückweise konstante Funktion für alle $t \ge t_0$ definiert ist: Das Abschmelzen der Schwelle, die Relaxation der Hysterese und das Auslaufen des gleitenden Fensters werden mit einer zeitlichen Auflösung von höchstens $\Delta_{eval}$ erfasst. Für die Belastungskennzahlen in Kapitel 5, die stationäre Erwartungswerte und keine Trajektorien sind, ist diese Diskretisierung ohne Belang; Eskalationslatenzen sind mit der Auflösung $\Delta_{eval}$ zu lesen.

Die angewendete Schwelle $\Theta_{current}(t)$ aktualisiert sich an jedem $t \in \mathcal{T}$ über ein asymmetrisches rekursives Filter erster Ordnung. Sei $t_{prev}$ der Zeitpunkt der vorhergehenden Auswertung und $\Delta t = t - t_{prev} \ge 0$ die seither verstrichene Zeit, gemessen an einer monotonen Uhr des Integrators (nicht an den Gerätezeitstempeln der Quellen, die gegeneinander verschoben sein können). Der Anfangszustand ist $\Theta_{current}(t_0) = \Theta_{target}(t_0)$. Für $t \in \mathcal{T}$, $t > t_0$, gilt:

$$
\Theta_{current}(t) = \begin{cases}
\Theta_{target}(t), & \Theta_{target}(t) \le \Theta_{current}(t_{prev}) \quad (\text{Fast Attack})\\
\Theta_{target}(t) + \big(\Theta_{current}(t_{prev}) - \Theta_{target}(t)\big)\cdot e^{-\rho(t)\cdot\Delta t}, & \text{sonst} \quad (\text{Slow Release})
\end{cases}
$$

mit $\rho(t) = \dfrac{1-SDC_{score}(t)}{T}$. Eine Absenkung der Zielschwelle wird damit unverzüglich übernommen, eine Anhebung nur verzögert; bei $SDC_{score} = 1$ wird $\rho = 0$, und die Schwelle erholt sich nicht, solange eine Bedingung höchster Priorität aktiv ist.

Der Slow-Release-Zweig lässt sich als Konvexkombination schreiben: Mit $\kappa = e^{-\rho(t)\Delta t} \in (0,1]$ gilt $\Theta_{current}(t) = (1-\kappa)\,\Theta_{target}(t) + \kappa\,\Theta_{current}(t_{prev})$, also $\Theta_{current}(t) \in \big[\Theta_{current}(t_{prev}),\, \Theta_{target}(t)\big]$. Da $\Theta_{target} > 0$ für alle $t$ und $\Theta_{current}(t_0) > 0$, folgt induktiv $\Theta_{current}(t) > 0$ für alle $t \ge t_0$ — die Eigenschaft, auf der die Dreiwertigkeit des Verdikts in 3.4.6 beruht.

*Anmerkung zu Einzelquellen.* Da $k_{min}$ die Energie skaliert, nicht die Kardinalität (3.4.4), bleibt eine Eskalation durch eine einzige Quelle möglich, sobald deren Gewicht die vollständig abgeschmolzene Schranke erreicht: $w_{max} \ge \delta_{min} \cdot k_{min} \cdot \bar w$. Für $|M| = 2$ verschwindet der Faktor $(|M|-2)$, sodass $k_{min} \equiv 2$ unabhängig von $SDC_{score}$ und die abgeschmolzene Schranke $\delta_{min} \cdot 2\bar w$ beträgt; mit $\delta_{min} = 0{,}5$ ist das genau $\bar w$, und wegen $w_{max} \ge \bar w$ kann in jedem Zweikanal-Ensemble die stärkere Quelle nach Ablauf eines Fensters $T$ allein eskalieren. Ein Schutz durch $\delta_{min}$ gegen Einzelquellen-Eskalation setzt $k_{min} \ge 3$ voraus, also $|M| \ge 3$ bei hinreichend niedrigem Schweregradindex.

### 3.4.6 Eskalationsbedingung

Der Algorithmus löst aktive Alarme in eine dreiwertige Weiterleitungsentscheidung auf:

**Systemeskalation (Rot):**
$$
E(t) \ge \Theta_{current}(t)
$$

**Lokale Bettplatzbenachrichtigung (Gelb, Fail-Safe-Warnung):**
$$
0 < E(t) < \Theta_{current}(t)
$$

Im Einklang mit IEC 60601-1-8 [21] wird kein aktiver lokaler Alarm stillschweigend unterdrückt: Liegt physiologische Evidenz vor, ohne dass die Schranke erreicht ist, verbleibt das Ereignis als Warnzustand am Bettplatz sichtbar, bis es entweder abklingt oder genügend Evidenz akkumuliert, um $\Theta_{current}(t)$ zu überschreiten.

**Ruhezustand ($E = 0$):** Sind alle zugelassenen Quellen über das gesamte Fenster inaktiv, gilt $E(t) = 0$. Da $\Theta_{current}(t) > 0$ für alle $t$ (3.4.5), liegt dieser Fall außerhalb beider vorstehenden Bedingungen; es wird weder eskaliert noch eine Bettplatzwarnung ausgegeben. Die drei Fälle bilden damit eine disjunkte und vollständige Zerlegung des Wertebereichs von $E(t)$.

**Halten des eskalierten Zustands (Latching).** Die Bedingung $E(t) \ge \Theta_{current}(t)$ definiert den Zeitpunkt, zu dem eine Eskalation *erklärt* wird. Einmal erklärt, wird die Eskalation *gehalten*, bis sämtliche beteiligten Alarmbedingungen beendet sind, statt bei jedem nachfolgenden Ereignis erneut gegen die Schwelle geprüft zu werden. Das Halten ist eine Eigenschaft der Schnittstelle zur Zentralstation, nicht des Filters selbst, und erfüllt zwei Zwecke: Es verhindert ein Flattern der weitergeleiteten Entscheidung, wenn $E(t)$ um die Schwelle oszilliert, und es stellt sicher, dass eine erklärte Krise nicht durch den Ausfall eines beteiligten Kanals stillschweigend zurückgenommen wird. Der Evidenzakkumulator verfolgt währenddessen weiterhin den tatsächlichen Zustand jeder Quelle, sodass $E(t)$, $SDC_{score}(t)$ und $\Theta_{current}(t)$ gültig bleiben; gehalten wird allein die Weiterleitungsentscheidung. Das Halten und das Anhalten von $\Delta t_{active}$ (3.4.5) unterliegen unterschiedlichen Bedingungen und können deshalb vorübergehend auseinanderfallen: Der mathematische Kern kann sich bereits nicht mehr als eskaliert betrachten, während die Schnittstelle den gehaltenen Zustand noch anzeigt.

Da die Eskalationsbedingung zu den Auswertungszeitpunkten $\mathcal{T}$ geprüft wird (3.4.5), ist der Zeitpunkt der ersten Eskalation nach Beginn der Überwachung als

$$
t_{esc} = \min\{\, t_n \in \mathcal{T} : E(t_n) \ge \Theta_{current}(t_n) \,\}
$$

definiert, mit $t_{esc} = +\infty$, falls die Menge leer ist. Da $\mathcal{T}_P$ ein äquidistantes Raster ist und in jedem endlichen Zeitintervall nur endlich viele Alarmereignisse eintreffen, enthält $\mathcal{T}$ in jedem endlichen Intervall nur endlich viele Punkte und besitzt keinen Häufungspunkt; jede nichtleere Teilmenge hat daher ein kleinstes Element, und das Minimum ist wohldefiniert. Auf diese Größe greift die Latenzauswertung in Kapitel 5 zurück.

---

## Literaturverzeichnis (Fortsetzung)

[25] W. Kuo und M. J. Zuo, *Optimal Reliability Modeling: Principles and Applications*, Wiley, Hoboken (NJ), 2003.
[26] Z. Chair und P. K. Varshney, „Optimal Data Fusion in Multiple Sensor Detection Systems", *IEEE Transactions on Aerospace and Electronic Systems*, vol. AES-22, no. 1, pp. 98–101, 1986. doi: 10.1109/TAES.1986.310699.
[27] A. Wald, „Sequential Tests of Statistical Hypotheses", *The Annals of Mathematical Statistics*, vol. 16, no. 2, pp. 117–186, 1945. doi: 10.1214/aoms/1177731118.
[28] A. Wald und J. Wolfowitz, „Optimum Character of the Sequential Probability Ratio Test", *The Annals of Mathematical Statistics*, vol. 19, no. 3, pp. 326–339, 1948. doi: 10.1214/aoms/1177730197.
[29] M. A. Richards, *Fundamentals of Radar Signal Processing*, McGraw-Hill, New York, 2005.
[30] G. D. Birkhoff, „Proof of the Ergodic Theorem", *Proceedings of the National Academy of Sciences*, vol. 17, no. 12, pp. 656–660, 1931. doi: 10.1073/pnas.17.2.656.
[31] P. K. Varshney, *Distributed Detection and Data Fusion*, Springer, New York, 1997.
[32] J. D. C. Little, „A Proof for the Queuing Formula: $L = \lambda W$", *Operations Research*, vol. 9, no. 3, pp. 383–387, 1961. doi: 10.1287/opre.9.3.383.
[33] A. Chintschin, „Zu Birkhoffs Lösung des Ergodenproblems", *Mathematische Annalen*, vol. 107, pp. 485–488, 1933. doi: 10.1007/BF01448905.
