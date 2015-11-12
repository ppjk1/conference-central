# Conference Central #

description...

## Requirements ##
- ...
- ...

## Quickstart ##
- ...
- ...

## What's Included ##
Within the download, you'll find the following files:
```
conference-central/
```


## Session, SessionForm, and Speaker Design Decisions ##

##### Session #####

Session entities are implemented as children of a single Conference entity. The relationship is established in two ways:

1. Session Keys contain an appropriate ancestor path.
2. A Session entity stores a websafe version of its parent Conference key.

Point 2 was decided upon to facilitate Session queries by speaker. When querying directly upon the Session Kind, it was desirable to make associated conference information easily retrievable. The *initial* design stored a list of Session keys in a Conference entity, but this proved untenable as additional query types were added.

##### SessionForm #####

SessionForm entities are straight copies of Session entities with the following exceptions:
- SessionForm includes a websafe version of its key as a parameter.
- SessionForm stores the typeOfSession parameter as an Enum (rather than as a string).

##### Speaker #####

Speakers are implemented as their own Kind. Each Session entity stores a list of Speaker keys via a repeated String property.

Speakers do not have parents, as they may be associated to many Session entities. They are thus considered **root entities**.


### Credits ###
