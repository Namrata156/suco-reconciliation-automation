/**
 * Drag-and-drop box for the upload page (UI refinements U4, 2.4): drop the
 * day's files or the whole folder. Native HTML drag and drop, no extra
 * package; a dropped folder is read through the browser's entry API (Edge
 * and Chrome, design 7.5), files in sub-folders included. The two file
 * pickers stay next to it as the fallback; the box itself also opens the
 * file picker with a click, Enter or Space.
 */
import { Text } from '@mantine/core';
import { useRef, useState, type DragEvent, type KeyboardEvent } from 'react';

/** Reads every file under a dropped entry (a file, or a folder and its sub-folders). */
async function filesOf(entry: FileSystemEntry): Promise<File[]> {
  if (entry.isFile) {
    return new Promise((resolve) =>
      (entry as FileSystemFileEntry).file(
        (f) => resolve([f]),
        () => resolve([]),
      ),
    );
  }
  if (!entry.isDirectory) return [];
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  const children: FileSystemEntry[] = [];
  // readEntries returns at most ~100 entries per call: read until it returns none.
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve) =>
      reader.readEntries(resolve, () => resolve([])),
    );
    if (batch.length === 0) break;
    children.push(...batch);
  }
  return (await Promise.all(children.map(filesOf))).flat();
}

/** The files of a drop: folders opened when the browser gives entries, else the plain file list. */
export async function collectDropped(dt: Pick<DataTransfer, 'items' | 'files'>): Promise<File[]> {
  const entries = Array.from(dt.items ?? [])
    .filter((i) => i.kind === 'file')
    .map((i) => (typeof i.webkitGetAsEntry === 'function' ? i.webkitGetAsEntry() : null));
  if (entries.length > 0 && entries.every((e) => e !== null)) {
    return (await Promise.all(entries.map((e) => filesOf(e!)))).flat();
  }
  return Array.from(dt.files ?? []);
}

export function DropZone({
  disabled,
  onFiles,
  onChooseFiles,
}: {
  disabled: boolean;
  onFiles: (files: File[]) => void;
  /** Opens the "Choose files" picker (click / keyboard on the box). */
  onChooseFiles: () => void;
}) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);

  const drop = async (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    depth.current = 0;
    setOver(false);
    if (disabled) return;
    const files = await collectDropped(e.dataTransfer);
    if (files.length > 0) onFiles(files);
  };
  const key = (e: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onChooseFiles();
    }
  };

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled}
      aria-label="Drop the day's files or folder here, or press Enter to choose files"
      onClick={() => !disabled && onChooseFiles()}
      onKeyDown={key}
      onDragEnter={(e) => {
        e.preventDefault();
        depth.current += 1;
        setOver(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setOver(false);
      }}
      onDrop={(e) => void drop(e)}
      style={{
        border: `2px dashed ${over ? 'var(--mantine-color-blue-6)' : 'var(--mantine-color-gray-5)'}`,
        background: over ? 'var(--mantine-color-blue-light)' : undefined,
        borderRadius: 8,
        padding: '28px 16px',
        textAlign: 'center',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <Text fw={600}>Drop the day&apos;s files or the whole folder here</Text>
      <Text size="sm" c="dimmed">
        or click to choose files
      </Text>
    </div>
  );
}
