-- Version 9 -> 10: use the English code for the final part of speech.
UPDATE senses SET pos='else' WHERE pos='其他';
