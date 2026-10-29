"""Boundary wiring guards; executable metadata/copy behavior lives in AssetAdmissionTests."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / "MCPForUnity/Editor/Services/AssetGen"


def source(relative: str) -> str:
    return (ASSET / relative).read_text(encoding="utf-8-sig")


def test_every_provider_and_remote_catalog_uses_structural_json_admission():
    for path in [*(ASSET / "Providers").glob("*Adapter.cs"), *(ASSET.glob("*ModelCatalog.cs"))]:
        text = path.read_text(encoding="utf-8-sig")
        assert "JObject.Parse(" not in text, path.name
        assert "JObject.Load(" not in text, path.name
    assert "BoundedJson.ParseObject" in source("Import/ModelDependencyBundle.cs")
    assert "BoundedJson.ParseObject" in source("Providers/TripoAdapter.cs")


def test_image_admission_runs_before_publication_and_native_import():
    prepared = source("PreparedArtifact.cs")
    create = prepared[prepared.index("private static PreparedArtifact CreateReserved(") :]
    assert create.index("DetectExtension(") < create.index("FileMode.CreateNew")
    detect = prepared[prepared.index("internal static string DetectExtension(") :]
    assert "new ImageAdmission().Inspect" in detect
    assert "ModelMetadataAdmission.Inspect" in detect
    image = source("Import/ImageImportPipeline.cs")
    assert image.index("new ImageAdmission().File") < image.index("AssetDatabase.ImportAsset")


def test_archive_inventory_is_admitted_before_extraction_and_recursive_import():
    model = source("Import/ModelImportPipeline.cs")
    archive = model[model.index("private static AssetGenJob ImportArchive(") :]
    assert archive.index("ArchiveAssetAdmission.Validate") < archive.index(
        "selectionComplete = true"
    )
    assert archive.index("ArchiveAssetAdmission.Validate") < archive.index(
        "AssetDatabase.ImportAsset"
    )
    extractor = source("Import/SafeZipExtractor.cs")
    assert extractor.index("inspectArchive?.Invoke") < extractor.index("FileMode.CreateNew")


def test_local_single_and_bundle_copy_share_project_ledger_before_creating_files():
    single = (ROOT / "MCPForUnity/Editor/Tools/AssetGen/ImportModelFile.cs").read_text()
    bundle = source("Import/ModelDependencyBundle.cs")
    for text in (single, bundle):
        assert "File.Copy(" not in text
        assert "new LocalAssetCopy(Path.GetDirectoryName(Application.dataPath)" in text
        assert text.index("new LocalAssetCopy(") < text.index("copying.Copy(")
    copy = source("Http/LocalAssetCopy.cs")
    assert "AssetDiskBudget.ReserveExtraction" in copy
    assert "FileMode.CreateNew" in copy
    assert "created.TrueForAll(AssetDiskBudget.FileIsGone)" in copy
    # Cleanup accounting is exercised by AssetDiskBudgetTests, including admission
    # after owner loss. Do not require the obsolete transfer-completion helper here.


def test_structural_admission_precedes_any_jtoken_materialization():
    text = source("BoundedJson.cs")
    assert text.index("Validate(text, limits)") < text.index("JObject.Load(")
    for budget in ("MaxTokens", "MaxProperties", "MaxContainerItems", "MaxDepth"):
        assert budget in text


def test_metadata_readers_never_call_untrusted_pixel_decoders():
    for relative in (
        "Import/ImageAdmission.cs",
        "Import/ArchiveAssetAdmission.cs",
        "Import/ModelMetadataAdmission.cs",
    ):
        text = source(relative)
        for forbidden in ("LoadImage(", "Texture2D(", "Image.FromStream(", "File.ReadAllBytes("):
            assert forbidden not in text, (relative, forbidden)
