import { NavLink, Route, Routes } from "react-router-dom";
import SuppliersPage from "./pages/SuppliersPage";
import UploadPage from "./pages/UploadPage";
import SupplierDetailPage from "./pages/SupplierDetailPage";

export default function App() {
  return (
    <>
      <header className="topbar">
        <span className="brand">Каталог · Приказ 838</span>
        <nav>
          <NavLink to="/" end>
            Поставщики
          </NavLink>
          <NavLink to="/upload">Загрузить прайс</NavLink>
        </nav>
      </header>
      <main className="container">
        <Routes>
          <Route path="/" element={<SuppliersPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/supplier/:id" element={<SupplierDetailPage />} />
        </Routes>
      </main>
    </>
  );
}
