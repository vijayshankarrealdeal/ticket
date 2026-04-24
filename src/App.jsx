import { useState } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { GlobalSearchProvider } from './hooks/useGlobalSearch.jsx';
import { useAnimationPref } from './hooks/useAnimationPref.js';
import Sidebar from './components/Sidebar.jsx';
import Conversations from './pages/Conversations.jsx';
import Tickets from './pages/Tickets.jsx';
import Customers from './pages/Customers.jsx';
import Teams from './pages/Teams.jsx';
import Platforms from './pages/Platforms.jsx';
import AgentBuilder from './pages/AgentBuilder.jsx';
import Settings from './pages/Settings.jsx';
import Analytics from './pages/Analytics.jsx';
import ChannelsLayout from './pages/channels/ChannelsLayout.jsx';
import ChannelsOverview from './pages/channels/ChannelsOverview.jsx';
import ECommerce from './pages/channels/ECommerce.jsx';
import ApiLibrary from './pages/channels/ApiLibrary.jsx';
import ApiLogs from './pages/channels/ApiLogs.jsx';
import ApiPlayground from './pages/channels/ApiPlayground.jsx';
import GoogleSheets from './pages/channels/GoogleSheets.jsx';
import Health from './pages/channels/Health.jsx';
import Notifications from './pages/Notifications.jsx';
import PublicCsat from './pages/PublicCsat.jsx';
import AuthPage, { loadAuth, saveAuth } from './pages/AuthPage.jsx';
import './animations.css';
import './App.css';

export default function App() {
  const [user, setUser] = useState(() => loadAuth());
  useAnimationPref();
  const location = useLocation();

  // Public CSAT survey is reachable without auth — the survey link is sent
  // to customers who do not have workspace accounts.
  if (location.pathname.startsWith('/csat/')) {
    return (
      <Routes>
        <Route path="/csat/:token" element={<PublicCsat />} />
      </Routes>
    );
  }

  if (!user) {
    return (
      <AuthPage
        onAuthenticated={(u) => {
          saveAuth(u);
          setUser(u);
        }}
      />
    );
  }

  return (
    <GlobalSearchProvider>
      <div className="app">
        <Sidebar />
        <div className="workspace">
          <Routes>
          <Route path="/" element={<Navigate to="/conversations" replace />} />
          <Route path="/conversations" element={<Conversations />} />
          <Route path="/tickets" element={<Tickets />} />
          <Route path="/customers" element={<Customers />} />
          <Route path="/teams" element={<Teams />} />
          <Route path="/platforms" element={<Platforms />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/agent-builder" element={<AgentBuilder />} />
          <Route path="/api-playground" element={<ApiPlayground />} />
          <Route path="/channels" element={<ChannelsLayout />}>
            <Route index element={<ChannelsOverview />} />
            <Route path="ecommerce" element={<ECommerce />} />
            <Route path="api-library" element={<ApiLibrary />} />
            <Route path="api-logs" element={<ApiLogs />} />
            <Route path="sheets" element={<GoogleSheets />} />
            <Route path="health" element={<Health />} />
          </Route>
          <Route path="/integrations" element={<Navigate to="/channels" replace />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/notifications" element={<Notifications />} />
          <Route path="*" element={<Navigate to="/conversations" replace />} />
          </Routes>
        </div>
      </div>
    </GlobalSearchProvider>
  );
}
