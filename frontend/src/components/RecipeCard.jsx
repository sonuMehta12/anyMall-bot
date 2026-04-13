import './RecipeCard.css'

/**
 * RecipeCard — displays one recipe as a horizontal card.
 *
 * Props:
 *   recipe: {id, title_ja, image_url, primary_protein, kcal_per_100g, description}
 *   petName: (optional) string — pet name for the chip label in dual-pet mode
 */
export default function RecipeCard({ recipe, petName = null }) {
  return (
    <div className="recipe-card">
      <div
        className="recipe-card-image"
        style={{
          backgroundImage: `url('${recipe.image_url}')`,
          backgroundSize: 'cover',
          backgroundPosition: 'center',
        }}
      />
      <div className="recipe-card-body">
        <div className="recipe-card-title">{recipe.title_ja}</div>
        <div className="recipe-card-meta">
          {recipe.primary_protein && (
            <>
              <span>🥩 {recipe.primary_protein}</span>
              {recipe.kcal_per_100g && <span> • </span>}
            </>
          )}
          {recipe.kcal_per_100g && (
            <span>{recipe.kcal_per_100g} kcal/100g</span>
          )}
        </div>
        {petName && <div className="recipe-card-pet-chip">{petName}</div>}
      </div>
    </div>
  )
}
