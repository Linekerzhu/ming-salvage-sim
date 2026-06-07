import Foundation

protocol TextGenerationServicing {
    func generateText(
        messages: [LLMChatMessage],
        temperature: Double?,
        maxTokens: Int?
    ) async throws -> String
}

protocol ImageGenerationServicing {
    func generateImage(prompt: String, imageSize: String?) async throws -> LLMImageGenerationResponse
}

struct TextGenerationService: TextGenerationServicing {
    let client: LLMHTTPClient

    func generateText(
        messages: [LLMChatMessage],
        temperature: Double? = nil,
        maxTokens: Int? = nil
    ) async throws -> String {
        let response = try await client.generateText(
            LLMTextGenerationRequest(
                messages: messages,
                temperature: temperature,
                maxTokens: maxTokens
            )
        )
        return response.text
    }
}

struct ImageGenerationService: ImageGenerationServicing {
    let client: LLMHTTPClient

    func generateImage(prompt: String, imageSize: String? = nil) async throws -> LLMImageGenerationResponse {
        return try await client.generateImage(
            LLMImageGenerationRequest(
                prompt: prompt,
                imageSize: imageSize
            )
        )
    }
}
