import SwiftUI

/// Recipe photos. `image_url` has been on recipes since the first ingest —
/// schema.org gives us one for nearly every parsed page — it just had nowhere
/// to appear. It's a remote URL the household doesn't own, so every use here
/// assumes it may be missing, slow, or dead, and degrades to the placeholder
/// rather than leaving a hole in the layout.
enum RecipeImageURL {
    /// Only http(s) is ever loaded. The API stores whatever a client sent
    /// (validation there stays deliberately loose so older clients keep
    /// working), so the check belongs here, at the point of use.
    static func parse(_ raw: String?) -> URL? {
        guard let raw, let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              url.host != nil
        else { return nil }
        return url
    }
}

/// The square thumbnail on a library row.
struct RecipeThumbnail: View {
    let imageUrl: String?
    var size: CGFloat = 52

    var body: some View {
        Group {
            if let url = RecipeImageURL.parse(imageUrl) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill()
                    case .failure:
                        placeholder
                    case .empty:
                        placeholder.overlay(ProgressView().controlSize(.small))
                    @unknown default:
                        placeholder
                    }
                }
            } else {
                placeholder
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private var placeholder: some View {
        Rectangle()
            .fill(.quaternary)
            .overlay(
                Image(systemName: "fork.knife")
                    .font(.system(size: size * 0.35))
                    .foregroundStyle(.secondary)
            )
    }
}

/// The wide photo at the top of a recipe. Renders nothing at all when there's
/// no usable URL — an empty grey band above every unphotographed recipe would
/// be worse than no band.
struct RecipeHeroImage: View {
    let imageUrl: String?

    var body: some View {
        if let url = RecipeImageURL.parse(imageUrl) {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image
                        .resizable()
                        .scaledToFill()
                        .frame(height: 200)
                        .clipped()
                case .failure:
                    EmptyView()
                case .empty:
                    ProgressView().frame(maxWidth: .infinity).frame(height: 200)
                @unknown default:
                    EmptyView()
                }
            }
            .frame(maxWidth: .infinity)
            .accessibilityHidden(true)
        }
    }
}
