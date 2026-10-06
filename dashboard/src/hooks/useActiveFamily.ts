import { useCallback, useEffect, useState } from "react";

import { homemindActiveFamilyApi } from "../api/modules/homemindActiveFamily";

let cachedFamilyId: string | null | undefined;
const listeners = new Set<(familyId: string | null) => void>();

function emit(value: string | null) {
  cachedFamilyId = value;
  for (const listener of listeners) listener(value);
}

async function fetchActiveFamily(): Promise<string | null> {
  try {
    const response = await homemindActiveFamilyApi.get();
    return response.family_id ?? null;
  } catch {
    return null;
  }
}

/** Per-user active family (stage 3) shared across the dashboard. */
export function useActiveFamily(): {
  familyId: string | null;
  loading: boolean;
  setFamily: (familyId: string) => Promise<void>;
  clear: () => Promise<void>;
  refresh: () => Promise<void>;
} {
  const [familyId, setFamilyId] = useState<string | null>(
    cachedFamilyId === undefined ? null : cachedFamilyId,
  );
  const [loading, setLoading] = useState(cachedFamilyId === undefined);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const next = await fetchActiveFamily();
      setFamilyId(next);
      emit(next);
    } finally {
      setLoading(false);
    }
  }, []);

  const setFamily = useCallback(async (next: string) => {
    setLoading(true);
    try {
      const response = await homemindActiveFamilyApi.set(next);
      setFamilyId(response.family_id ?? null);
      emit(response.family_id ?? null);
    } finally {
      setLoading(false);
    }
  }, []);

  const clear = useCallback(async () => {
    setLoading(true);
    try {
      await homemindActiveFamilyApi.clear();
      setFamilyId(null);
      emit(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (cachedFamilyId === undefined) {
      void refresh();
    } else {
      setFamilyId(cachedFamilyId);
    }
    const listener = (value: string | null) => setFamilyId(value);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, [refresh]);

  return { familyId, loading, setFamily, clear, refresh };
}
