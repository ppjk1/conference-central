# Conference Central

description...

## Requirements
- ...
- ...

## Quickstart
- ...
- ...

## What's Included
Within the download, you'll find the following files:
```
conference-central/
```

****

## Session, SessionForm, and Speaker Design Decisions

##### Session - inherits from `ndb.Model`

Session entities are implemented as children of a single Conference entity. The relationship is established in two ways:

1. Session Keys contain an appropriate ancestor path.
2. A Session entity stores a websafe version of its parent Conference key.

Point 2 was decided upon to facilitate Session queries by speaker. When querying directly upon the Session Kind, it was desirable to make associated conference information easily retrievable. The *initial* design stored a list of Session keys in a Conference entity, but this proved untenable as additional query types were added.

**Restrictions:**

- Session created is restricted to conference organizer
- `name` is required
- If `startDate` and `endDate` are defined on the parent Conference entity, then the Session `date` must fall within conference dates.
- `startTime` should be entered as a time string of the format HH:MM in 24-hour format. For datastore, the time string is converted to integer seconds. Two conversion functions were added to `utils.py` to facilitate turning a time string like `"12:00"` into integer seconds and back again. This was done to ease time comparisons for the 'query problem' seen below from Task 3.
- `typeOfSession` is implemented as an Enum and accepts the following values:
    - `NOT_SPECIFIED`
    - `KEYNOTE`
    - `LECTURE`
    - `DEMONSTRATION`
    - `PANEL`
    - `WORKSHOP`
    - `ROUNDTABLE`

##### SessionForm - inherits from `messages.Message`

SessionForm entities are straight copies of Session entities with the following exceptions:
- SessionForm includes a websafe version of its key as a parameter.
- SessionForm stores the typeOfSession parameter as an Enum (rather than as a string).
- `date` is converted from integer seconds to a time string.

##### Speaker - inherits from `ndb.Model`

Speakers are implemented as their own Kind.

The Speaker-Session relationship may be described as many-to-many.
- Each Session entity stores a list of Speaker keys via a repeated String property.
- Speakers do not have parents, as they may be associated to many Session entities. They are thus considered **root entities**.

`name` is the only required property.

Additional helper classes include `SpeakerForm` and `SpeakerForms`, both of which inherit from `messages.Message`.

If a reviewer wishes to add speakers to a session, they must add the websafe Speaker keys, retrievable via the `getSpeakers` endpoint (or admin console on localhost).

****

## Session Wishlist

The session wishlist is implemented as a list of websafe Session keys stored as a parameter on the Profile kind.

Wishlists are open to any session and are not limited to those conferences for which the user is registered.

Queries are implemented to retrieve either all sessions in a user's wishlist, or only those sessions in the user's wishlist that belong to a given conference.

****

## Additional Queries

1. **getConferencesByOrganizer(organizer)**: a variation on `getConferencesCreated`, letting the user specify the organizer.
    - **Justification**: If a user attends a conference and loves it, they will want to find similar conferences. Chances are, the conference was great because it was well-organized and well-run, which has more to do with the organizer than the topic or location (organizer determines both).

    - **Caution**: Accepts the `displayName` for an organizer, as this is the data the front end clients most likely display to a user. However, names are not required to be unique, so in the event multiple Profile entities have the same name, Conferences will only be returned for the first matching organizer found.

2. **getSessionsPopular(websafeConferenceKey)**: returns top three sessions for a conference, rated by frequency with which they appear in user wishlists, sorted by descending popularity.
    - **Justification**: If many users found a specific session interesting, chances are other users will, too.

    - Does not return rating info, just the sorted SessionForm objects.

    - For sessions with equal popularity, will return the sessions in the order they appear in the `Session.query()` results.

****

## Query-Related Problem

The following situation was suggested:

> Let’s say that you don't like workshops and you don't like sessions after 7 pm. How would you handle a query for all non-workshop sessions before 7 pm?

### Problems for Implementing This Query

The query requires multiple inequality filters:
1. `Session.typeOfSession != 'WORKSHOP'`
2. `Session.startTime < '19:00'`

If both filters are applied to a single query, this will raise a `BadRequestError`.

**Ndb only supports one inequality filter per query.**

If it is not obvious how the first filter constitutes an inequality filter, we must reference the [documentation][1] to understand the implementation of the operation.

The `!=` operation is implemented as an `ndb.OR` operation on a set of inequality filters. Filter 1 above is the equivalent of:
```
ndb.OR(Session.typeOfSession < 'WORKSHOP',
       Session.typeOfSession > 'WORKSHOP')
```

When paired with the second filter, this violates the rule of one inequality filter per query.

### Solutions

##### Implemented Solution - see `conference.py`

Instead of using an *inequality* filter to filter out a single value, use a set of *equality* filters for every other session type except the one we're filtering out.

We can do this using `ndb.query.FilterNodes` stored in a list and passed into our query. We know the length of the list because our session types were stored as an Enum, so it will be the number of values in the Enum minus 1.

Use `ndb.OR` to tie the equality filters together and `ndb.AND` to tie them them to the `startTime` filter.

```
type_filters = []
for session_type in TypeOfSession:
    if str(session_type) != request.notTypeOfSession:
        filter_node = ndb.query.FilterNode('typeOfSession', '=', str(session_type))
        type_filters.append(filter_node)

q = Session.query(ndb.OR(
                        ndb.AND(timeFilter, type_filters[0]),
                        ndb.AND(timeFilter, type_filters[1]),
                        ndb.AND(timeFilter, type_filters[2]),
                        ndb.AND(timeFilter, type_filters[3]),
                        ndb.AND(timeFilter, type_filters[4]),
                        ndb.AND(timeFilter, type_filters[5])))
```

##### Alternate Solution (not implemented)

Use multiple queries: one for the time filter and another for the session type filter. Then compare them against each other using Python code to find the appropriate results.

****

### Credits

- [Python: converting time strings to integers][2]
- [Python: converting integer seconds to time string][3]
- [Python: converting string representation of list to list][4]
- Starter Conference app code provided by Udacity.

[1]: https://cloud.google.com/appengine/docs/python/ndb/queries#neq_and_in
[2]: http://stackoverflow.com/questions/6402812/how-to-convert-an-hmmss-time-string-to-seconds-in-python
[3]: http://stackoverflow.com/questions/775049/python-time-seconds-to-hms
[4]: http://stackoverflow.com/questions/1894269/convert-string-representation-of-list-to-list-in-python
