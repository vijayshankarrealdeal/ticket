import { createContext, useContext, useMemo, useState } from 'react';

const Ctx = createContext({ query: '', setQuery: () => {} });

export function GlobalSearchProvider({ children }) {
  const [query, setQuery] = useState('');
  const value = useMemo(() => ({ query, setQuery }), [query]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useGlobalSearch() {
  return useContext(Ctx);
}
