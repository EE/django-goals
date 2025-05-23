When we use DB to keep track of goals execution we gather more and more records there. These numbers can be large in comparison to the size of bussiness data the goals are operating at.

We need to remove goals that are no longer needed. But what makes the goal "no longer needed"?

First approach is to assume that achieved goals that were completed long ago can be deleted.
Why dont delete achieved AND FAILED goals? Becuase we are treating FKs pointing to Goal, containg null as "succesfull goal". Business logic needs to know wether goal is succesful even if it was completed long ago and we deleted it. So deleted goals leave behine null FKs. And we treat these nulls as still pointing to succesful goals.

So we cannot delete old faile goal, becuase it could be interpreted then as succesful. The empty FK it left behind would be mistaken for "completed long ago and deleted".

So maybe we should create a special guardian FAILED_GOAL instance and use it to fill references to old failed deleted goal? thats one possibility.

---

Next option is to do garbage colloecton on all goals. Unreferenced goals will be nuked.
 This is good becuase we keep audit trail even for old goals. But this means all goals must be referenced in other models -- so no classic "pass instructions as json, and forget".
