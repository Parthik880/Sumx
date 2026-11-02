# Summary modes and spoken action items

## Summary modes

`backend/summary_modes.py` sets a target from the transcript's word count. Standard is 9.5% of it, between 150 and 1,100 words. Concise is 25% of Standard and Detailed is 175%. Each mode has its own prompt in `backend/provider.py`, with an overview length, a number of discussion points and an output-token budget.

Transcripts longer than `SUMMARY_CHUNK_CHARS` become notes first, and all notes then go into one final call that sets the length. Only very long meetings merge in groups, and notes are never split. If a response is cut off or malformed, it retries once with double the token budget, and both calls are recorded in the cost ledger. If a result is over 125% of target, one shortening pass runs. Summaries are never padded, so a short meeting can fall below target.

Each summary stores its `detail` and `prompt_version`, which includes the prompt and model. Requesting the same mode for the same transcript, prompt and model reuses that summary with no LLM call. Older and hand-edited summaries have no mode and are not reused. Raise `PROMPT_VERSION` after changing a prompt.

Narration uses the meeting's current summary. Saved audio is matched on a hash of the spoken words, so another mode or older wording never matches, and returning to a mode reuses its audio.

## Spoken action items

`spoken_action` in `backend/schema.py` builds each sentence from the structured fields:

| Owner | Deadline | Spoken |
| --- | --- | --- |
| None | None | Prepare the annual budget report. |
| John | None | John will prepare the annual budget report. |
| None | Friday | Submit the project proposal by Friday. |
| John | Friday | John will submit the project proposal by Friday. |

Values such as null, empty, `N/A`, `unknown`, `not specified` and `TBD` count as missing. Stored meeting data is unchanged. Only whole clauses about a missing owner or deadline are removed from a task, so ordinary words stay. A list item that is only a placeholder is skipped. If a task does not start with a verb, it is read as "John will handle this: ...".

The LLM is also told to use null for missing values, to start tasks with a verb, and to list only future work as action items.

## Measured results

Same 6,479-word, 50-minute transcript through the real LLM. Each run took about 14–20 s per mode and cost $0.002–$0.004.

| Run | Concise | Standard | Detailed |
| --- | --- | --- | --- |
| Before | 874 | 629 | 778 |
| Final prompts | 140 | 499 | 1,257 |
| Final prompts, 19,034-word meeting | 135 | 986 | 1,203 |

Word counts are narration words. The order held in both final runs. Lengths vary between runs and can fall outside the 20–30% and 150–200% goals: on the 19,034-word meeting, Concise was 14% of Standard and Detailed 122%, and Concise and Detailed were well under target. No summary is padded to reach a target.

Every owner and deadline in the checked summaries appeared in the transcript. That check does not prove every statement is accurate, so summaries still need human review.

## Media conversion

Extraction takes about 0.5 s per 3-minute chunk and does not speed up with FFmpeg threads, so threading was left unchanged. Transcription is covered in the [speech guide](MODAL_TTS.md).
