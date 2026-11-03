import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { Layout } from "./components/Layout.tsx";
import { AccountPage } from "./pages/AccountPage.tsx";
import { CaseDetailPage } from "./pages/CaseDetailPage.tsx";
import { CaseQueuePage } from "./pages/CaseQueuePage.tsx";
import { TransferPage } from "./pages/TransferPage.tsx";
import { NotFound } from "./components/States.tsx";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<CaseQueuePage />} />
          <Route path="cases/:id" element={<CaseDetailPage />} />
          <Route path="accounts" element={<AccountPage />} />
          <Route path="accounts/:id" element={<AccountPage />} />
          <Route path="transfers/:id" element={<TransferPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
