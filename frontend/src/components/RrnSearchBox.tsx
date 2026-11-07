/**
 * "Find an RRN" in the header on every page (UI refinements U3, 4.5): Enter
 * opens /<module>/rrn/<rrn> (contract B1), which shows the RRN's
 * transactions, cases and file rows, or opens its one transaction.
 */
import { TextInput } from '@mantine/core';
import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router';

/** Trim spaces and a leading apostrophe (an RRN pasted from Excel), as the server does. */
export function cleanRrn(raw: string): string {
  return raw.trim().replace(/^'+/, '').trim();
}

export function RrnSearchBox({ moduleSlug }: { moduleSlug: string }) {
  const [value, setValue] = useState('');
  const navigate = useNavigate();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const rrn = cleanRrn(value);
    if (!rrn) return;
    navigate(`/${moduleSlug}/rrn/${encodeURIComponent(rrn)}`);
    setValue('');
  };
  return (
    <form onSubmit={submit} role="search" aria-label="Find an RRN">
      <TextInput
        aria-label="Find an RRN"
        placeholder="Find an RRN"
        value={value}
        onChange={(e) => setValue(e.currentTarget.value)}
        w={170}
        size="sm"
      />
    </form>
  );
}
