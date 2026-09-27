import { Route, Routes } from "react-router-dom";
import { usePrograms } from "./api/hooks";
import { SourceViewer } from "./components/Evidence";
import { TopBar } from "./components/TopBar";
import { AiPage } from "./pages/AiPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { MapPage, evidenceRatio, useGraphForUi } from "./pages/MapPage";
import { MatrixPage } from "./pages/MatrixPage";
import { ReviewsPage } from "./pages/ReviewsPage";
import { ScenariosPage } from "./pages/ScenariosPage";
import { TablePage } from "./pages/TablePage";
import { useUi } from "./store/ui";

export function App() {
  const programId = useUi((s) => s.programId);
  const programs = usePrograms();
  const program = programs.data?.find((p) => p.id === programId);
  const graph = useGraphForUi();
  return (
    <div className="app">
      {program?.is_synthetic ? (
        <div className="synthetic-banner" role="status">
          SYNTHETIC FIXTURE: “{program.name}” is invented test data and is not University of Calgary curriculum data.
        </div>
      ) : <div />}
      <TopBar evidenceRatio={evidenceRatio(graph.data)} />
      <Routes>
        <Route path="/" element={<MapPage />} />
        <Route path="/table" element={<TablePage />} />
        <Route path="/matrix" element={<MatrixPage />} />
        <Route path="/scenarios/*" element={<ScenariosPage />} />
        <Route path="/reviews" element={<ReviewsPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/ai" element={<AiPage />} />
      </Routes>
      <SourceViewer />
    </div>
  );
}
