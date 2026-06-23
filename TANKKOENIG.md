# Intro
Historische Benzinpreisdaten in Deutschland als CSV-Dateien.
Die Preise werden von [Tankerkönig](<https://www.tankerkoenig.de>) als Datensammlung unter einer Creativecommons-Lizenz zur Verfügung gestellt.
## Tankerkönig
[Tankerkönig](<https://www.tankerkoenig.de>) bietet auch ein Echtzeit-Benzinpreis-API als REST-Service an, der leicht in Apps, Websites und Enterprise-Systeme integriert werden kann.

# Daten holen per Download oder git
Das gesamte Repository ist ausgepackt über 50 GB groß und damit etwas unhandlich. 
## Download
Wer die Daten einmalig benötigt, lädt sie am einfachsten über die Weboberfläche runter.
## git clone
Da jede Nacht die Daten des letzten Tages dazukommen, ist ein täglicher Download ineffizient. Besser ist es das git-Repository einmalig zu clonen und dann jeden Tag mit `git pull` upzudaten.

Von der Kommandozeile:

`git clone https://tankerkoenig@dev.azure.com/tankerkoenig/tankerkoenig-data/_git/tankerkoenig-data`

Falls es timeout-Fehler gibt, kann man auch `git clone --depth 1 https://tankerkoenig@dev.azure.com/tankerkoenig/tankerkoenig-data/_git/tankerkoenig-data` probieren, das clont die history nicht mit und dauert nicht so lange.

die tägichen Updates holt man dann mit

`git pull`

Wenn man `git pull` täglich per cron-job ausführt, hat man immer alle aktuellen Daten da.

# Feedback
Wir freuen uns über jede Rückmeldung zur Verwendung der Daten. Wir linken auch gerne auf Projekte, welche diese Daten verwenden. Auch Verbeserungsvorschläge oder anderes Feedback sind gern gesehen, wir sind gespannt, was man mit den Daten machen kann!

# Preise
Im Verzeichnis _prices_ sind alle Preisänderungen aller Tankstellen in jeweils einer CSV-Datei pro Tag protokolliert.

Felder im CSV-Header:

`date,station_uuid,diesel,e5,e10,dieselchange,e5change,e10change`

Bedeutung der Felder:

|Feld        |Bedeutung                                      |
|------------|-----------------------------------------------|
|date        |Änderungszeitpunkt                             |
|station_uuid|UUID der Tankstelle aus `stations`             |
|diesel      |Preis Diesel                                   |
|e5          |Preis Super E5                                 |
|e10         |Preis Super E10                                |
|dieselchange|0=keine Änderung, 1=Änderung, 2=Entfernt, 3=Neu|
|e5change    |0=keine Änderung, 1=Änderung, 2=Entfernt, 3=Neu|
|e10change   |0=keine Änderung, 1=Änderung, 2=Entfernt, 3=Neu|


# Tankstellen
Im Verzeichnis _stations_ sind alle Tankstellen in einer CSV-Datei aufgeführt. 
Da sich die Tankstellenliste ändert, wird sie täglich in ein Verzeichnis stations/JAHR/MONAT/ exportiert

Felder im CSV-Header:

`uuid,name,brand,street,house_number,post_code,city,latitude,longitude`

Bedeutung:

|Feld             |Bedeutung                                      |
|-----------------|-----------------------------------------------|
|uuid             |UUID der Tankstelle, matcht mit den Preisen    |
|name             |Tankstellenname                                |
|brand            |Marke                                          |
|street           |Straße                                         |
|post_code        |Postleitzahl                                   |
|city             |Stadt                                          |
|latitude         |geogr. Breite                                  |
|longitude        |geogr. Länge                                   |
|first_active     |erstes Auftauchen                              |
|openingtimes_json|Öffnungszeiten als JSON                        |

# openingtimes_json
applicable_days: die Tage, an denen diese Öffnungszeit gültig ist, binär kodiert - ein Byte.

|Wert             |Bedeutung                                      |
|-----------------|-----------------------------------------------|
|1|Montag|
|2|Dienstag|
|4|Mittwoch|
|8|Donnerstag|
|16|Freitag|
|32|Samstag|
|64|Sonntag|
|128|Feiertag|

Bsp:
* Montag - Freitag = 31 (1 + 2 + 4 + 8 + 16)
* Sa/So = 96 (32 + 64)
* Feiertags = 128

# Zuordnung
Jede Tankstelle ist eindeutig über eine UUID identifiziert. In den Preisdaten wird diese UUID referenziert.

# Lizenz der Datensammlung
<https://creativecommons.org/licenses/by-nc-sa/4.0/>
Für kommerzielle Nutzung stellen wir die Daten unter anderer Lizenz zur Verfügung. Nachfrage unter <info@tankerkoenig.de>
